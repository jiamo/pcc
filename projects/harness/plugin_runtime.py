"""Reactive Cordis-style composition kernel for PCC Harness.

The kernel owns two independent dimensions. Service realms determine where a
capability is visible. Plugin fibers determine when a plugin is active and
which effects must be withdrawn when its committed providers change.
"""


class EventDispatchError(RuntimeError):
    """All parallel listeners ran, and one or more of them failed."""

    def __init__(self, event_name: str, failures) -> None:
        self.event_name = event_name
        self.failures = failures.copy()
        RuntimeError.__init__(
            self,
            "parallel event "
            + event_name
            + " failed in "
            + str(len(failures))
            + " listener(s)",
        )


class DependencyResolutionError(RuntimeError):
    """A caller required a service that is absent from its current realm."""

    def __init__(self, consumer: str, service: str, scope: str, realm: str) -> None:
        self.consumer = consumer
        self.service = service
        self.scope = scope
        self.realm = realm
        RuntimeError.__init__(
            self,
            "plugin "
            + consumer
            + " requires unavailable service "
            + service
            + " in realm "
            + realm
            + " at "
            + scope,
        )


class Effect:
    """One setup operation and its idempotent reverse-order cleanup."""

    def __init__(self, kind: str = "effect", setup=None) -> None:
        self.kind = kind
        self.disposers = []
        self.active = True
        self.owner = None
        self.name = ""
        self.realm = ""
        self.value = None
        self.relationship = ""
        self.provider_id = 0
        self.event_name = ""
        self.mode = ""
        self.callback = None
        self.global_listener = False
        self.label = ""
        self.setting_up = False
        self.dispose_requested = False
        if self.kind == "effect" and setup is not None:
            self.start(setup)

    def start(self, setup) -> None:
        """Run setup after the owner has made this effect observable."""
        if self.kind != "effect" or not self.active:
            raise RuntimeError("effect cannot be started")
        self.setting_up = True
        try:
            self._collect(setup())
        except Exception:
            self.setting_up = False
            try:
                self._dispose_now()
            except Exception:
                pass
            raise
        self.setting_up = False
        if self.dispose_requested:
            self._dispose_now()

    def _collect(self, produced) -> None:
        if produced is None:
            return
        if callable(produced):
            self.disposers.append(produced)
            return
        try:
            iterator = iter(produced)
        except TypeError:
            raise TypeError("invalid effect result")
        while True:
            try:
                disposer = next(iterator)
            except StopIteration:
                return
            if not callable(disposer):
                raise TypeError("invalid effect disposer")
            self.disposers.append(disposer)

    def dispose(self) -> None:
        if not self.active:
            return
        if self.setting_up:
            self.dispose_requested = True
            return
        self._dispose_now()

    def _dispose_now(self) -> None:
        if not self.active:
            return
        self.active = False
        if self.kind == "service":
            self.owner.root._service_changed(self.name, self.realm)
            return
        if self.kind == "event":
            return
        first_failure = None
        i = len(self.disposers) - 1
        while i >= 0:
            try:
                self.disposers[i]()
            except Exception as error:
                if first_failure is None:
                    first_failure = error
            i -= 1
        if first_failure is not None:
            raise first_failure

class PluginContext:
    """Hierarchical effect owner with inherited service-realm selections."""

    def __init__(self, name: str, parent=None) -> None:
        self.name = name
        self.parent = parent
        self.children = []
        self.effects = []
        self.active = True
        self.plugin_fiber = None
        self.isolation_names = []
        self.isolation_realms = []
        self.disposing = False
        if parent is None:
            self.root = self
            self.kernel = None
            self.service_registrations = []
            self.event_registrations = []
            self.next_realm_id = 1
            self.next_provider_id = 1
        else:
            self.root = parent.root
            parent.children.append(self)
            i = 0
            while i < len(parent.isolation_names):
                self.isolation_names.append(parent.isolation_names[i])
                self.isolation_realms.append(parent.isolation_realms[i])
                i += 1

    def path(self) -> str:
        if self.parent is None:
            return self.name
        return self.parent.path() + "/" + self.name

    def child(self, name: str):
        self._require_active()
        return PluginContext(name, self)

    def isolate(self, name: str, label: str = ""):
        """Create a child selecting a private or explicitly joined realm."""
        self._require_active()
        if name == "":
            raise ValueError("isolated service name must not be empty")
        child = PluginContext("isolate:" + name, self)
        realm = label
        if realm == "":
            realm = "private:" + name + ":" + str(self.root.next_realm_id)
            self.root.next_realm_id += 1
        child._select_realm(name, realm)
        return child

    def isolate_service(self, name: str, label: str = "") -> str:
        """Select a private or joined service realm on an empty context."""
        self._require_active()
        if len(self.children) > 0 or len(self.effects) > 0:
            raise RuntimeError("service isolation must precede context use")
        if name == "":
            raise ValueError("isolated service name must not be empty")
        realm = label
        if realm == "":
            realm = "private:" + name + ":" + str(self.root.next_realm_id)
            self.root.next_realm_id += 1
        self._select_realm(name, realm)
        return realm

    def effect(self, setup, label: str = "anonymous") -> Effect:
        """Run setup now and own every disposer it returns."""
        self._require_active()
        effect = Effect("effect")
        effect.owner = self
        effect.label = label
        self.effects.append(effect)
        try:
            effect.start(setup)
        except Exception:
            self._remove_effect(effect)
            raise
        return effect

    def provide(self, name: str, value, relationship: str = "") -> Effect:
        """Publish one service implementation in this context's realm."""
        self._require_active()
        realm = self.service_realm(name)
        registrations = self.root.service_registrations
        i = 0
        while i < len(registrations):
            current = registrations[i]
            if current.active and current.name == name and current.realm == realm:
                raise ValueError(
                    "service "
                    + name
                    + " is already registered in realm "
                    + realm
                    + " by "
                    + current.owner.path()
                )
            i += 1
        provider_id = self.root.next_provider_id
        self.root.next_provider_id += 1
        registration = Effect("service")
        registration.owner = self
        registration.name = name
        registration.realm = realm
        registration.value = value
        registration.relationship = relationship
        registration.provider_id = provider_id
        registration.label = "provide:" + name
        registrations.append(registration)
        self.effects.append(registration)
        if self.plugin_fiber is None or self.plugin_fiber.state == "ACTIVE":
            self.root._service_changed(name, realm)
        return registration

    def get(self, name: str):
        """Read a service from the selected realm.

        An unloading consumer retains its committed provider so its cleanup can
        finish after public withdrawal and before the provider resource closes.
        """
        self._require_active_or_unloading()
        committed = self._committed_registration(name)
        if committed is not None:
            return committed.value
        registration = self._find_registration(name, True)
        if registration is not None:
            return registration.value
        raise KeyError(
            "service is not registered: "
            + name
            + " (realm "
            + self.service_realm(name)
            + ", scope "
            + self.path()
            + ")"
        )

    def require(self, name: str, consumer: str = ""):
        """Resolve one declared dependency with an actionable diagnostic."""
        try:
            return self.get(name)
        except KeyError:
            owner = consumer
            if owner == "":
                owner = self.path()
            raise DependencyResolutionError(
                owner, name, self.path(), self.service_realm(name)
            )

    def has(self, name: str) -> bool:
        try:
            self.get(name)
            return True
        except KeyError:
            return False

    def service_realm(self, name: str) -> str:
        i = len(self.isolation_names) - 1
        while i >= 0:
            if self.isolation_names[i] == name:
                return self.isolation_realms[i]
            i -= 1
        return "root:" + name

    def service_relationship(self, name: str) -> str:
        self._require_active_or_unloading()
        committed = self._committed_registration(name)
        if committed is not None:
            return committed.relationship
        registration = self._find_registration(name, True)
        if registration is not None:
            return registration.relationship
        raise KeyError("service is not registered: " + name)

    def set_service(self, name: str, value) -> None:
        """Update a service only from the context that provided it."""
        self._require_active()
        registration = self._find_registration(name, True)
        if registration is None:
            raise KeyError("cannot set service without provide: " + name)
        if registration.owner is not self:
            raise RuntimeError("cannot set service owned by another context: " + name)
        registration.value = value
        self.root._service_changed(name, registration.realm)

    def on(
        self,
        event_name: str,
        callback,
        mode: str = "emit",
        prepend: bool = False,
        global_listener: bool = False,
    ) -> Effect:
        """Register an owned listener for one dispatch strategy."""
        self._require_active()
        if (
            mode != "emit"
            and mode != "parallel"
            and mode != "serial"
            and mode != "bail"
            and mode != "waterfall"
        ):
            raise ValueError("unsupported event mode: " + mode)
        registration = Effect("event")
        registration.owner = self
        registration.event_name = event_name
        registration.mode = mode
        registration.callback = callback
        registration.global_listener = global_listener
        registration.label = "on:" + event_name + ":" + mode
        if prepend:
            self.root.event_registrations.insert(0, registration)
        else:
            self.root.event_registrations.append(registration)
        self.effects.append(registration)
        return registration

    def once(
        self,
        event_name: str,
        callback,
        mode: str = "emit",
        prepend: bool = False,
        global_listener: bool = False,
    ) -> Effect:
        """Register a listener that removes itself before its first callback."""
        holder = []

        def run_once(payload):
            holder[0].dispose()
            return callback(payload)

        registration = self.on(
            event_name, run_once, mode, prepend, global_listener
        )
        holder.append(registration)
        return registration

    def emit(self, event_name: str, payload) -> None:
        listeners = self._listeners(event_name, "emit")
        i = 0
        while i < len(listeners):
            listeners[i].callback(payload)
            i += 1

    def parallel(self, event_name: str, payload) -> None:
        """Attempt every listener and aggregate failures deterministically."""
        listeners = self._listeners(event_name, "parallel")
        failures = []
        i = 0
        while i < len(listeners):
            try:
                listeners[i].callback(payload)
            except Exception as error:
                failures.append(error)
            i += 1
        if len(failures) > 0:
            raise EventDispatchError(event_name, failures)

    def serial(self, event_name: str, payload):
        """Run listeners in order and return the first bail value."""
        return self._bail_dispatch(event_name, payload, "serial")

    def bail(self, event_name: str, payload):
        """Synchronously return the first non-None, non-False result."""
        return self._bail_dispatch(event_name, payload, "bail")

    def waterfall(self, event_name: str, payload, terminal):
        """Compose listeners; only an explicit next call reaches the suffix."""
        listeners = self._listeners(event_name, "waterfall")
        return self._waterfall_at(listeners, 0, payload, terminal)

    def _waterfall_at(self, listeners, index: int, payload, terminal):
        if index >= len(listeners):
            return terminal(payload)

        def next_listener(next_payload=payload):
            return self._waterfall_at(
                listeners, index + 1, next_payload, terminal
            )

        return listeners[index].callback(payload, next_listener)

    def dispose(self) -> None:
        """Retire descendants and effects while continuing after cleanup errors."""
        if not self.active or self.disposing:
            return
        self.disposing = True
        first_failure = None
        i = len(self.children) - 1
        while i >= 0:
            try:
                self.children[i].dispose()
            except Exception as error:
                if first_failure is None:
                    first_failure = error
            i -= 1
        i = len(self.effects) - 1
        while i >= 0:
            try:
                self.effects[i].dispose()
            except Exception as error:
                if first_failure is None:
                    first_failure = error
            i -= 1
        self.active = False
        self.disposing = False
        if first_failure is not None:
            raise first_failure

    def _remove_effect(self, target) -> None:
        i = 0
        while i < len(self.effects):
            if self.effects[i] is target:
                self.effects.pop(i)
                return
            i += 1

    def _bail_dispatch(self, event_name: str, payload, mode: str):
        listeners = self._listeners(event_name, mode)
        i = 0
        while i < len(listeners):
            result = listeners[i].callback(payload)
            if result is not None and result is not False:
                return result
            i += 1
        return None

    def _listeners(self, event_name: str, mode: str):
        listeners = []
        registrations = self.root.event_registrations
        i = 0
        while i < len(registrations):
            registration = registrations[i]
            if (
                registration.active
                and registration.event_name == event_name
                and registration.mode == mode
                and (
                    registration.global_listener
                    or self._is_descendant_of(registration.owner)
                )
            ):
                listeners.append(registration)
            i += 1
        return listeners

    def _find_registration(self, name: str, allow_self_loading: bool):
        realm = self.service_realm(name)
        registrations = self.root.service_registrations
        i = len(registrations) - 1
        while i >= 0:
            registration = registrations[i]
            if (
                registration.active
                and registration.name == name
                and registration.realm == realm
            ):
                fiber = registration.owner.plugin_fiber
                if fiber is None or fiber.state == "ACTIVE":
                    return registration
                if allow_self_loading and registration.owner is self:
                    return registration
            i -= 1
        return None

    def _owns_service(self, name: str) -> bool:
        realm = self.service_realm(name)
        registrations = self.root.service_registrations
        i = 0
        while i < len(registrations):
            registration = registrations[i]
            if (
                registration.active
                and registration.owner is self
                and registration.name == name
                and registration.realm == realm
            ):
                return True
            i += 1
        return False

    def _committed_registration(self, name: str):
        fiber = self.plugin_fiber
        if fiber is None:
            return None
        i = 0
        while i < len(fiber.inject_names):
            if fiber.inject_names[i] == name and i < len(fiber.committed):
                return fiber.committed[i]
            i += 1
        return None

    def _select_realm(self, name: str, realm: str) -> None:
        i = len(self.isolation_names) - 1
        while i >= 0:
            if self.isolation_names[i] == name:
                self.isolation_realms[i] = realm
                return
            i -= 1
        self.isolation_names.append(name)
        self.isolation_realms.append(realm)

    def _is_descendant_of(self, possible_ancestor) -> bool:
        current = self
        while current is not None:
            if current is possible_ancestor:
                return True
            current = current.parent
        return False

    def _service_changed(self, name: str, realm: str) -> None:
        if self.kernel is not None:
            self.kernel._service_changed(name, realm)

    def _require_active(self) -> None:
        if not self.active or self.disposing:
            raise RuntimeError("plugin context is disposed: " + self.path())

    def _require_active_or_unloading(self) -> None:
        if self.active:
            return
        fiber = self.plugin_fiber
        if fiber is not None and fiber.state == "UNLOADING":
            return
        raise RuntimeError("plugin context is disposed: " + self.path())


class PluginFiber:
    """One installed plugin definition and its current activation instance."""

    def __init__(
        self,
        name: str,
        plugin,
        parent,
        requires,
        inject_names,
        provides,
    ) -> None:
        self.name = name
        self.plugin = plugin
        self.parent = parent
        self.requires = requires.copy()
        self.inject_names = inject_names.copy()
        self.provides = provides.copy()
        self.state = "PENDING"
        self.context = None
        self.committed = []
        self.failed_provider_ids = []
        self.last_error = ""
        self.installed = True


class PluginKernel:
    """Owner and reconciler for the live plugin/service graph."""

    def __init__(self) -> None:
        self.context = PluginContext("harness")
        self.context.kernel = self
        self.plugin_fibers = []
        self.reconciling = False
        self.reconcile_requested = False

    def install(
        self,
        name: str,
        plugin,
        requires=None,
        inject=None,
        provides=None,
        parent=None,
    ):
        """Install a fiber; unavailable injected services leave it pending."""
        if requires is None:
            requires = []
        if inject is None:
            inject = []
        if provides is None:
            provides = []
        if parent is None:
            parent = self.context
        if self._index_of(name) >= 0:
            raise ValueError("plugin already installed: " + name)
        missing = []
        i = 0
        while i < len(requires):
            dependency = requires[i]
            if dependency == name:
                raise ValueError("plugin dependency cycle: " + name + " -> " + name)
            if self._index_of(dependency) < 0:
                missing.append(dependency)
            i += 1
        if len(missing) > 0:
            raise ValueError(
                "plugin " + name + " requires missing plugin(s): " + ", ".join(missing)
            )
        fiber = PluginFiber(
            name, plugin, parent, requires, inject, provides
        )
        self.plugin_fibers.append(fiber)
        try:
            self._refresh_fiber(fiber, True)
        except Exception:
            self._remove_fiber(fiber)
            raise
        return fiber.context

    def install_all(self, specifications):
        """Validate plugin dependencies, then install in stable graph order."""
        names = []
        i = 0
        while i < len(specifications):
            spec = specifications[i]
            if spec.name == "":
                raise ValueError("plugin name must not be empty")
            if self._contains(names, spec.name) or self._index_of(spec.name) >= 0:
                raise ValueError("duplicate plugin specification: " + spec.name)
            names.append(spec.name)
            i += 1
        i = 0
        while i < len(specifications):
            spec = specifications[i]
            j = 0
            while j < len(spec.requires):
                dependency = spec.requires[j]
                if not self._contains(names, dependency) and self._index_of(dependency) < 0:
                    raise ValueError(
                        "plugin " + spec.name + " requires missing plugin: " + dependency
                    )
                j += 1
            i += 1

        pending = specifications.copy()
        ordered = []
        available = self.installed_plugins()
        while len(pending) > 0:
            selected = -1
            i = 0
            while i < len(pending):
                if self._requirements_available(pending[i].requires, available):
                    selected = i
                    break
                i += 1
            if selected < 0:
                cycle = []
                i = 0
                while i < len(pending):
                    cycle.append(pending[i].name)
                    i += 1
                raise ValueError("plugin dependency cycle: " + " -> ".join(cycle))
            spec = pending.pop(selected)
            ordered.append(spec)
            available.append(spec.name)

        installed = []
        try:
            i = 0
            while i < len(ordered):
                spec = ordered[i]
                self.install(
                    spec.name,
                    spec.plugin,
                    spec.requires,
                    spec.inject,
                    spec.provides,
                    spec.parent,
                )
                installed.append(spec.name)
                i += 1
        except Exception:
            i = len(installed) - 1
            while i >= 0:
                self._unload_unchecked(installed[i])
                i -= 1
            raise
        return installed

    def unload(self, name: str) -> None:
        """Unload one plugin definition; injected consumers become pending."""
        index = self._index_of(name)
        if index < 0:
            return
        dependents = []
        i = 0
        while i < len(self.plugin_fibers):
            fiber = self.plugin_fibers[i]
            if fiber.installed and self._contains(fiber.requires, name):
                dependents.append(fiber.name)
            i += 1
        if len(dependents) > 0:
            raise RuntimeError(
                "cannot unload plugin " + name + "; required by: " + ", ".join(dependents)
            )
        self._unload_unchecked(name)

    def reload(
        self,
        name: str,
        plugin,
        requires=None,
        inject=None,
        provides=None,
    ):
        """Replace one leaf definition with a new fiber identity."""
        index = self._index_of(name)
        if index < 0:
            raise KeyError("plugin is not installed: " + name)
        old = self.plugin_fibers[index]
        old_requires = old.requires.copy()
        old_inject = old.inject_names.copy()
        old_provides = old.provides.copy()
        old_parent = old.parent
        self.unload(name)
        return self.install(
            name,
            plugin,
            old_requires if requires is None else requires,
            old_inject if inject is None else inject,
            old_provides if provides is None else provides,
            old_parent,
        )

    def active_plugins(self):
        names = []
        i = 0
        while i < len(self.plugin_fibers):
            fiber = self.plugin_fibers[i]
            if fiber.installed and fiber.state == "ACTIVE":
                names.append(fiber.name)
            i += 1
        return names

    def installed_plugins(self):
        names = []
        i = 0
        while i < len(self.plugin_fibers):
            if self.plugin_fibers[i].installed:
                names.append(self.plugin_fibers[i].name)
            i += 1
        return names

    def plugin_state(self, name: str) -> str:
        index = self._index_of(name)
        if index < 0:
            return "ABSENT"
        return self.plugin_fibers[index].state

    def dependency_diagnostics(self):
        """Describe every pending or failed fiber and its selected realms."""
        diagnostics = []
        i = 0
        while i < len(self.plugin_fibers):
            fiber = self.plugin_fibers[i]
            if fiber.installed and fiber.state != "ACTIVE":
                if fiber.last_error != "":
                    diagnostics.append(
                        fiber.name + " [" + fiber.state + "]: " + fiber.last_error
                    )
                j = 0
                while j < len(fiber.inject_names):
                    service = fiber.inject_names[j]
                    registration = self._resolve_registration(fiber.parent, service)
                    if registration is None:
                        diagnostics.append(
                            fiber.name
                            + " [PENDING] requires "
                            + service
                            + " in realm "
                            + fiber.parent.service_realm(service)
                            + " at "
                            + fiber.parent.path()
                        )
                    j += 1
            i += 1
        return diagnostics

    def graph_snapshot(self):
        """Return a deterministic diagnostic projection of the live graph."""
        plugins = []
        i = 0
        while i < len(self.plugin_fibers):
            fiber = self.plugin_fibers[i]
            if fiber.installed:
                injections = []
                j = 0
                while j < len(fiber.inject_names):
                    service = fiber.inject_names[j]
                    registration = None
                    if j < len(fiber.committed):
                        registration = fiber.committed[j]
                    if registration is None:
                        registration = self._resolve_registration(fiber.parent, service)
                    provider = ""
                    provider_id = 0
                    relationship = ""
                    committed = j < len(fiber.committed)
                    if registration is not None:
                        provider = self._effect_owner_name(registration)
                        provider_id = registration.provider_id
                        relationship = registration.relationship
                    injections.append(
                        {
                            "service": service,
                            "realm": fiber.parent.service_realm(service),
                            "provider": provider,
                            "providerId": provider_id,
                            "relationship": relationship,
                            "committed": committed,
                        }
                    )
                    j += 1
                scope_path = fiber.parent.path() + "/" + fiber.name
                if fiber.context is not None:
                    scope_path = fiber.context.path()
                plugins.append(
                    {
                        "name": fiber.name,
                        "state": fiber.state,
                        "scope": scope_path,
                        "requires": fiber.requires.copy(),
                        "inject": injections,
                        "provides": fiber.provides.copy(),
                        "lastError": fiber.last_error,
                    }
                )
            i += 1

        services = []
        registrations = self.context.service_registrations
        i = 0
        while i < len(registrations):
            registration = registrations[i]
            if registration.active:
                provider_fiber = registration.owner.plugin_fiber
                published = provider_fiber is None or provider_fiber.state == "ACTIVE"
                services.append(
                    {
                        "name": registration.name,
                        "realm": registration.realm,
                        "provider": self._effect_owner_name(registration),
                        "providerId": registration.provider_id,
                        "relationship": registration.relationship,
                        "published": published,
                    }
                )
            i += 1

        effects = []
        self._collect_effect_snapshot(self.context, effects)
        return {"plugins": plugins, "services": services, "effects": effects}

    def assert_healthy(self) -> None:
        diagnostics = self.dependency_diagnostics()
        if len(diagnostics) > 0:
            raise RuntimeError("plugin graph is not healthy: " + "; ".join(diagnostics))

    def dispose(self) -> None:
        first_failure = None
        i = len(self.plugin_fibers) - 1
        while i >= 0:
            name = self.plugin_fibers[i].name
            try:
                self._unload_unchecked(name)
            except Exception as error:
                if first_failure is None:
                    first_failure = error
            i -= 1
        try:
            self.context.dispose()
        except Exception as error:
            if first_failure is None:
                first_failure = error
        if first_failure is not None:
            raise first_failure

    def _refresh_fiber(self, fiber, propagate: bool) -> bool:
        if not fiber.installed:
            return False
        resolved = self._resolve_injections(fiber)
        if resolved is None:
            if fiber.state == "ACTIVE":
                self._deactivate(fiber, "PENDING", propagate)
                return True
            if fiber.state != "PENDING":
                fiber.state = "PENDING"
                return True
            return False
        provider_ids = self._provider_ids(resolved)
        if fiber.state == "ACTIVE":
            if self._same_values(provider_ids, self._provider_ids(fiber.committed)):
                return False
            self._deactivate(fiber, "PENDING", propagate)
        if fiber.state == "FAILED":
            if self._same_values(provider_ids, fiber.failed_provider_ids):
                return False
            fiber.state = "PENDING"
        if fiber.state == "PENDING":
            self._activate(fiber, resolved, propagate)
            return True
        return False

    def _activate(self, fiber, resolved, propagate: bool) -> None:
        scope = fiber.parent.child(fiber.name)
        scope.plugin_fiber = fiber
        fiber.context = scope
        fiber.committed = resolved.copy()
        fiber.state = "LOADING"
        fiber.last_error = ""
        try:
            fiber.plugin(scope)
            i = 0
            while i < len(fiber.provides):
                if not scope._owns_service(fiber.provides[i]):
                    raise RuntimeError(
                        "plugin "
                        + fiber.name
                        + " declared but did not provide service "
                        + fiber.provides[i]
                    )
                i += 1
            fiber.state = "ACTIVE"
            self._publish_scope_services(scope)
        except Exception as error:
            try:
                scope.dispose()
            except Exception:
                pass
            fiber.context = None
            fiber.committed = []
            fiber.state = "FAILED"
            fiber.failed_provider_ids = self._provider_ids(resolved)
            fiber.last_error = str(error)
            if propagate:
                raise

    def _deactivate(self, fiber, next_state: str, propagate: bool) -> None:
        scope = fiber.context
        fiber.state = "UNLOADING"
        failure = None
        if scope is not None:
            try:
                scope.dispose()
            except Exception as error:
                failure = error
        fiber.context = None
        fiber.committed = []
        fiber.state = next_state
        if failure is not None:
            fiber.last_error = str(failure)
            if propagate:
                raise failure

    def _service_changed(self, name: str, realm: str) -> None:
        self.reconcile_requested = True
        if self.reconciling:
            return
        self._reconcile()

    def _reconcile(self) -> None:
        self.reconciling = True
        limit = len(self.plugin_fibers) * 8 + 8
        rounds = 0
        try:
            while self.reconcile_requested:
                self.reconcile_requested = False
                i = 0
                while i < len(self.plugin_fibers):
                    self._refresh_fiber(self.plugin_fibers[i], False)
                    i += 1
                rounds += 1
                if rounds > limit:
                    raise RuntimeError("plugin graph did not converge")
        finally:
            self.reconciling = False

    def _resolve_injections(self, fiber):
        resolved = []
        i = 0
        while i < len(fiber.inject_names):
            registration = self._resolve_registration(
                fiber.parent, fiber.inject_names[i]
            )
            if registration is None:
                return None
            resolved.append(registration)
            i += 1
        return resolved

    def _resolve_registration(self, context, name: str):
        return context._find_registration(name, False)

    def _publish_scope_services(self, scope) -> None:
        registrations = self.context.service_registrations
        i = 0
        while i < len(registrations):
            registration = registrations[i]
            if registration.active and registration.owner is scope:
                self._service_changed(registration.name, registration.realm)
            i += 1

    def _provider_ids(self, registrations):
        values = []
        i = 0
        while i < len(registrations):
            values.append(registrations[i].provider_id)
            i += 1
        return values

    def _effect_owner_name(self, effect) -> str:
        fiber = effect.owner.plugin_fiber
        if fiber is not None:
            return fiber.name
        return effect.owner.path()

    def _collect_effect_snapshot(self, context, output) -> None:
        if not context.active and not context.disposing:
            return
        i = 0
        while i < len(context.effects):
            effect = context.effects[i]
            if effect.active:
                output.append(
                    {
                        "scope": context.path(),
                        "kind": effect.kind,
                        "label": effect.label,
                    }
                )
            i += 1
        i = 0
        while i < len(context.children):
            self._collect_effect_snapshot(context.children[i], output)
            i += 1

    def _same_values(self, left, right) -> bool:
        if len(left) != len(right):
            return False
        i = 0
        while i < len(left):
            if left[i] != right[i]:
                return False
            i += 1
        return True

    def _unload_unchecked(self, name: str) -> None:
        index = self._index_of(name)
        if index < 0:
            return
        fiber = self.plugin_fibers[index]
        fiber.installed = False
        failure = None
        if fiber.state == "ACTIVE" or fiber.state == "LOADING":
            try:
                self._deactivate(fiber, "INACTIVE", True)
            except Exception as error:
                failure = error
        else:
            fiber.state = "INACTIVE"
        self.plugin_fibers.pop(index)
        if failure is not None:
            raise failure

    def _remove_fiber(self, fiber) -> None:
        i = 0
        while i < len(self.plugin_fibers):
            if self.plugin_fibers[i] is fiber:
                self.plugin_fibers.pop(i)
                return
            i += 1

    def _requirements_available(self, requirements, available) -> bool:
        i = 0
        while i < len(requirements):
            if not self._contains(available, requirements[i]):
                return False
            i += 1
        return True

    def _contains(self, values, target: str) -> bool:
        i = 0
        while i < len(values):
            if values[i] == target:
                return True
            i += 1
        return False

    def _index_of(self, name: str) -> int:
        i = 0
        while i < len(self.plugin_fibers):
            fiber = self.plugin_fibers[i]
            if fiber.installed and fiber.name == name:
                return i
            i += 1
        return -1


class PluginSpec:
    """Declarative plugin definition used by the graph reconciler."""

    def __init__(
        self,
        name: str,
        plugin,
        requires=None,
        inject=None,
        provides=None,
        parent=None,
    ) -> None:
        self.name = name
        self.plugin = plugin
        self.requires = [] if requires is None else requires.copy()
        self.inject = [] if inject is None else inject.copy()
        self.provides = [] if provides is None else provides.copy()
        self.parent = parent
