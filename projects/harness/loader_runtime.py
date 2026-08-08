"""Declarative Cordis entry composition and transactional reconciliation."""

import json


class PluginCatalogRegistration:
    """Single-shot ownership handle for one plugin catalog contribution."""

    def __init__(self, catalog, name: str) -> None:
        self.catalog = catalog
        self.name = name
        self.active = True

    def dispose(self) -> None:
        if not self.active:
            return
        self.active = False
        self.catalog._remove(self.name)


class PluginCatalog:
    """Name-to-plugin registry used by declarative Loader entries."""

    def __init__(self) -> None:
        self.names = []
        self.plugins = []

    def register(self, name: str, plugin) -> PluginCatalogRegistration:
        if name == "":
            raise ValueError("plugin catalog name must not be empty")
        if self._index_of(name) >= 0:
            raise ValueError("plugin catalog name is already registered: " + name)
        self.names.append(name)
        self.plugins.append(plugin)
        return PluginCatalogRegistration(self, name)

    def resolve(self, name: str):
        index = self._index_of(name)
        if index < 0:
            raise KeyError("plugin is not registered in catalog: " + name)
        return self.plugins[index]

    def _remove(self, name: str) -> None:
        index = self._index_of(name)
        if index < 0:
            return
        self.names.pop(index)
        self.plugins.pop(index)

    def _index_of(self, name: str) -> int:
        i = 0
        while i < len(self.names):
            if self.names[i] == name:
                return i
            i += 1
        return -1


class LoaderEntry:
    """One declarative group or plugin mount in a Loader tree."""

    def __init__(
        self,
        entry_id: str,
        name: str = "",
        config=None,
        requires=None,
        inject=None,
        provides=None,
        children=None,
        group: bool = False,
        disabled: bool = False,
        isolate_names=None,
        isolate_realms=None,
    ) -> None:
        self.entry_id = entry_id
        self.name = name
        self.config = {} if config is None else config
        self.requires = [] if requires is None else requires.copy()
        self.inject = [] if inject is None else inject.copy()
        self.provides = [] if provides is None else provides.copy()
        self.children = [] if children is None else children.copy()
        self.group = group
        self.disabled = disabled
        self.isolate_names = (
            [] if isolate_names is None else isolate_names.copy()
        )
        self.isolate_realms = (
            [] if isolate_realms is None else isolate_realms.copy()
        )

    def signature(self) -> str:
        """Return the complete replacement identity used by reconciliation."""
        payload = {
            "id": self.entry_id,
            "name": self.name,
            "config": self.config,
            "requires": self.requires,
            "inject": self.inject,
            "provides": self.provides,
            "group": self.group,
            "disabled": self.disabled,
            "isolateNames": self.isolate_names,
            "isolateRealms": self.isolate_realms,
        }
        return json.dumps(payload, sort_keys=True)


class EntryPatch:
    """Replace, insert or remove one complete Loader entry by id."""

    def __init__(
        self,
        entry_id: str,
        replacement=None,
        after: str = "",
        remove: bool = False,
    ) -> None:
        self.entry_id = entry_id
        self.replacement = replacement
        self.after = after
        self.remove = remove


def apply_entry_patch(entries, patch: EntryPatch):
    """Return a copied entry tree after applying one id-addressed patch."""
    result = _copy_entry_tree(entries)
    location = _find_entry_location(result, patch.entry_id)
    if location is not None:
        siblings = location[0]
        index = location[1]
        if patch.remove:
            siblings.pop(index)
            return result
        if patch.replacement is None:
            raise ValueError("replacement patch requires an entry")
        if patch.replacement.entry_id != patch.entry_id:
            raise ValueError("replacement entry id must match patch id")
        siblings[index] = patch.replacement
        return result
    if patch.remove:
        raise KeyError("cannot remove missing entry: " + patch.entry_id)
    if patch.replacement is None:
        raise ValueError("insert patch requires an entry")
    if patch.replacement.entry_id != patch.entry_id:
        raise ValueError("inserted entry id must match patch id")
    if patch.after == "":
        result.append(patch.replacement)
        return result
    anchor = _find_entry_location(result, patch.after)
    if anchor is None:
        raise KeyError("patch anchor is missing: " + patch.after)
    anchor[0].insert(anchor[1] + 1, patch.replacement)
    return result


def apply_patch_layers(entries, layers):
    """Apply Profile, user and CLI patch layers in declared order."""
    result = _copy_entry_tree(entries)
    i = 0
    while i < len(layers):
        layer = layers[i]
        j = 0
        while j < len(layer):
            result = apply_entry_patch(result, layer[j])
            j += 1
        i += 1
    return result


def _find_entry_location(entries, entry_id: str):
    i = 0
    while i < len(entries):
        entry = entries[i]
        if entry.entry_id == entry_id:
            return (entries, i)
        nested = _find_entry_location(entry.children, entry_id)
        if nested is not None:
            return nested
        i += 1
    return None


def _copy_entry_tree(entries):
    result = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        result.append(
            LoaderEntry(
                entry.entry_id,
                entry.name,
                entry.config,
                entry.requires,
                entry.inject,
                entry.provides,
                _copy_entry_tree(entry.children),
                entry.group,
                entry.disabled,
                entry.isolate_names,
                entry.isolate_realms,
            )
        )
        i += 1
    return result


def entry_tree_data(entries):
    """Project Loader entries into a JSON-compatible diagnostic tree."""
    result = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        result.append(
            {
                "id": entry.entry_id,
                "name": entry.name,
                "config": entry.config,
                "requires": entry.requires.copy(),
                "inject": entry.inject.copy(),
                "provides": entry.provides.copy(),
                "group": entry.group,
                "disabled": entry.disabled,
                "isolateNames": entry.isolate_names.copy(),
                "isolateRealms": entry.isolate_realms.copy(),
                "children": entry_tree_data(entry.children),
            }
        )
        i += 1
    return result


def dump_entry_tree(entries) -> str:
    """Serialize the exact composed Loader tree for runtime diagnostics."""
    return json.dumps(entry_tree_data(entries), sort_keys=True, ensure_ascii=False)


class Bundle:
    """Named distribution layer contributing complete Loader entries."""

    def __init__(self, name: str, entries) -> None:
        self.name = name
        self.entries = _copy_entry_tree(entries)


class RuntimeProfile:
    """Ordered Bundle selection plus the Profile-owned patch layer."""

    def __init__(self, name: str, bundles, patches=None) -> None:
        self.name = name
        self.bundles = bundles.copy()
        self.patches = [] if patches is None else patches.copy()


class BundleRegistration:
    """Single-shot ownership handle for one Bundle definition."""

    def __init__(self, composer, name: str) -> None:
        self.composer = composer
        self.name = name
        self.active = True

    def dispose(self) -> None:
        if not self.active:
            return
        self.active = False
        self.composer._remove_bundle(self.name)


class ProfileComposer:
    """Compose Bundle, Profile, home, CLI and launcher patch layers."""

    def __init__(self) -> None:
        self.bundle_names = []
        self.bundles = []

    def register_bundle(self, bundle: Bundle) -> BundleRegistration:
        if bundle.name == "":
            raise ValueError("bundle name must not be empty")
        if self._bundle_index(bundle.name) >= 0:
            raise ValueError("bundle is already registered: " + bundle.name)
        self.bundle_names.append(bundle.name)
        self.bundles.append(bundle)
        return BundleRegistration(self, bundle.name)

    def compose(
        self,
        profile: RuntimeProfile,
        home_patches=None,
        cli_patches=None,
        launcher_patches=None,
    ):
        """Return the final tree after applying each precedence layer."""
        if home_patches is None:
            home_patches = []
        if cli_patches is None:
            cli_patches = []
        if launcher_patches is None:
            launcher_patches = []
        result = []
        i = 0
        while i < len(profile.bundles):
            bundle = self._resolve_bundle(profile.bundles[i])
            j = 0
            while j < len(bundle.entries):
                entry = bundle.entries[j]
                result = apply_entry_patch(
                    result, EntryPatch(entry.entry_id, entry)
                )
                j += 1
            i += 1
        return apply_patch_layers(
            result,
            [
                profile.patches,
                home_patches,
                cli_patches,
                launcher_patches,
            ],
        )

    def _resolve_bundle(self, name: str) -> Bundle:
        index = self._bundle_index(name)
        if index < 0:
            raise KeyError("profile bundle is not registered: " + name)
        return self.bundles[index]

    def _remove_bundle(self, name: str) -> None:
        index = self._bundle_index(name)
        if index < 0:
            return
        self.bundle_names.pop(index)
        self.bundles.pop(index)

    def _bundle_index(self, name: str) -> int:
        i = 0
        while i < len(self.bundle_names):
            if self.bundle_names[i] == name:
                return i
            i += 1
        return -1


class LoaderRecord:
    """Flattened entry plus its owning group and active mount."""

    def __init__(self, entry, parent_id: str) -> None:
        self.entry = entry
        self.parent_id = parent_id
        self.context = None

    def signature(self) -> str:
        return self.parent_id + "\0" + self.entry.signature()


class PluginLoader:
    """Incrementally reconcile declarative entries into a PluginKernel."""

    def __init__(self, kernel, catalog: PluginCatalog) -> None:
        self.kernel = kernel
        self.catalog = catalog
        self.records = []

    def reconcile(self, entries) -> None:
        """Apply one desired tree or restore the previous stable suffix."""
        desired = self._prepare(entries)
        prefix = self._common_prefix(self.records, desired)
        old_suffix = self.records[prefix:]
        failure = self._unmount(old_suffix)
        if failure is not None:
            raise failure
        self.records = self.records[:prefix]
        installed = []
        try:
            i = prefix
            while i < len(desired):
                record = desired[i]
                self._mount(record)
                self.records.append(record)
                installed.append(record)
                i += 1
        except Exception as error:
            self._unmount(installed)
            self.records = self.records[:prefix]
            rollback_failure = None
            i = 0
            while i < len(old_suffix):
                try:
                    self._mount(old_suffix[i])
                    self.records.append(old_suffix[i])
                except Exception as rollback_error:
                    rollback_failure = rollback_error
                    break
                i += 1
            if rollback_failure is not None:
                raise RuntimeError(
                    "loader update failed and rollback did not restore the tree: "
                    + str(error)
                    + "; rollback: "
                    + str(rollback_failure)
                )
            raise error

    def dispose(self) -> None:
        failure = self._unmount(self.records)
        self.records = []
        if failure is not None:
            raise failure

    def mounted_entries(self):
        values = []
        i = 0
        while i < len(self.records):
            values.append(self.records[i].entry.entry_id)
            i += 1
        return values

    def _prepare(self, entries):
        records = []
        self._flatten(entries, "", False, records)
        self._validate(records)
        return self._stable_dependency_order(records)

    def _flatten(self, entries, parent_id: str, parent_disabled: bool, output) -> None:
        i = 0
        while i < len(entries):
            entry = entries[i]
            disabled = parent_disabled or entry.disabled
            if not disabled:
                output.append(LoaderRecord(entry, parent_id))
                next_parent = parent_id
                if entry.group:
                    next_parent = entry.entry_id
                self._flatten(entry.children, next_parent, False, output)
            i += 1

    def _validate(self, records) -> None:
        names = []
        groups = []
        plugins = []
        i = 0
        while i < len(records):
            entry = records[i].entry
            if entry.entry_id == "":
                raise ValueError("loader entry id must not be empty")
            if self._contains(names, entry.entry_id):
                raise ValueError("duplicate loader entry id: " + entry.entry_id)
            names.append(entry.entry_id)
            if len(entry.isolate_names) != len(entry.isolate_realms):
                raise ValueError(
                    "isolation names and realms differ for " + entry.entry_id
                )
            if entry.group:
                if entry.name != "":
                    raise ValueError("group entry must not name a plugin: " + entry.entry_id)
                if len(entry.requires) > 0 or len(entry.inject) > 0 or len(entry.provides) > 0:
                    raise ValueError("group entry cannot declare plugin dependencies: " + entry.entry_id)
                groups.append(entry.entry_id)
            else:
                if entry.name == "":
                    raise ValueError("plugin entry requires a catalog name: " + entry.entry_id)
                if len(entry.children) > 0:
                    raise ValueError(
                        "plugin-entry children require the full parent-fiber loader: "
                        + entry.entry_id
                    )
                self.catalog.resolve(entry.name)
                plugins.append(entry.entry_id)
            i += 1
        i = 0
        while i < len(records):
            record = records[i]
            if record.parent_id != "" and not self._contains(groups, record.parent_id):
                raise ValueError("loader parent group is missing: " + record.parent_id)
            j = 0
            while j < len(record.entry.requires):
                required = record.entry.requires[j]
                if not self._contains(plugins, required):
                    raise ValueError(
                        "plugin "
                        + record.entry.entry_id
                        + " requires missing plugin entry "
                        + required
                    )
                j += 1
            i += 1

    def _stable_dependency_order(self, records):
        pending = records.copy()
        ordered = []
        available = []
        while len(pending) > 0:
            selected = -1
            i = 0
            while i < len(pending):
                record = pending[i]
                ready = record.parent_id == "" or self._contains(
                    available, record.parent_id
                )
                j = 0
                while ready and j < len(record.entry.requires):
                    if not self._contains(available, record.entry.requires[j]):
                        ready = False
                    j += 1
                if ready:
                    selected = i
                    break
                i += 1
            if selected < 0:
                cycle = []
                i = 0
                while i < len(pending):
                    cycle.append(pending[i].entry.entry_id)
                    i += 1
                raise ValueError("loader dependency cycle: " + " -> ".join(cycle))
            record = pending.pop(selected)
            ordered.append(record)
            available.append(record.entry.entry_id)
        return ordered

    def _mount(self, record) -> None:
        entry = record.entry
        parent = self.kernel.context
        if record.parent_id != "":
            parent_record = self._record(record.parent_id)
            if parent_record is None or parent_record.context is None:
                raise RuntimeError("loader parent is not mounted: " + record.parent_id)
            parent = parent_record.context
        if entry.group:
            context = parent.child(entry.entry_id)
            i = 0
            while i < len(entry.isolate_names):
                context.isolate_service(
                    entry.isolate_names[i], entry.isolate_realms[i]
                )
                i += 1
            record.context = context
            return
        plugin = self.catalog.resolve(entry.name)
        config = entry.config

        def configured(scope):
            return plugin(scope, config)

        record.context = self.kernel.install(
            entry.entry_id,
            configured,
            entry.requires,
            entry.inject,
            entry.provides,
            parent,
        )

    def _unmount(self, records):
        first_failure = None
        i = len(records) - 1
        while i >= 0:
            record = records[i]
            try:
                if record.entry.group:
                    if record.context is not None:
                        record.context.dispose()
                else:
                    self.kernel._unload_unchecked(record.entry.entry_id)
            except Exception as error:
                if first_failure is None:
                    first_failure = error
            record.context = None
            i -= 1
        return first_failure

    def _record(self, entry_id: str):
        i = 0
        while i < len(self.records):
            if self.records[i].entry.entry_id == entry_id:
                return self.records[i]
            i += 1
        return None

    def _common_prefix(self, current, desired) -> int:
        limit = len(current)
        if len(desired) < limit:
            limit = len(desired)
        i = 0
        while i < limit:
            if current[i].signature() != desired[i].signature():
                return i
            i += 1
        return limit

    def _contains(self, values, target: str) -> bool:
        i = 0
        while i < len(values):
            if values[i] == target:
                return True
            i += 1
        return False
