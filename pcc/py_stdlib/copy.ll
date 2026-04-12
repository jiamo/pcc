; ModuleID = "copy"
target triple = "unknown-unknown-unknown"
target datalayout = ""

@.cpy.modref.annotations = internal global ptr null

@.pystr.1 = internal constant [14 x i8] [i8 83, i8 104, i8 97, i8 108, i8 108, i8 111, i8 119, i8 32, i8 99, i8 111, i8 112, i8 121, i8 46, i8 0]

@.pyattr.__copy__ = internal constant [9 x i8] [i8 95, i8 95, i8 99, i8 111, i8 112, i8 121, i8 95, i8 95, i8 0]

@.cpy.attr.__copy__ = internal constant [9 x i8] [i8 95, i8 95, i8 99, i8 111, i8 112, i8 121, i8 95, i8 95, i8 0]

@.pystr.2 = internal constant [165 x i8] [i8 82, i8 101, i8 99, i8 117, i8 114, i8 115, i8 105, i8 118, i8 101, i8 32, i8 100, i8 101, i8 101, i8 112, i8 99, i8 111, i8 112, i8 121, i8 32, i8 111, i8 118, i8 101, i8 114, i8 32, i8 108, i8 105, i8 115, i8 116, i8 47, i8 100, i8 105, i8 99, i8 116, i8 47, i8 116, i8 117, i8 112, i8 108, i8 101, i8 47, i8 115, i8 101, i8 116, i8 32, i8 43, i8 32, i8 117, i8 115, i8 101, i8 114, i8 32, i8 111, i8 98, i8 106, i8 101, i8 99, i8 116, i8 115, i8 32, i8 119, i8 105, i8 116, i8 104, i8 10, i8 32, i8 32, i8 32, i8 32, i8 96, i8 96, i8 95, i8 95, i8 100, i8 101, i8 101, i8 112, i8 99, i8 111, i8 112, i8 121, i8 95, i8 95, i8 96, i8 96, i8 46, i8 32, i8 83, i8 104, i8 97, i8 114, i8 101, i8 100, i8 32, i8 114, i8 101, i8 102, i8 101, i8 114, i8 101, i8 110, i8 99, i8 101, i8 115, i8 32, i8 97, i8 114, i8 101, i8 32, i8 116, i8 114, i8 97, i8 99, i8 107, i8 101, i8 100, i8 32, i8 118, i8 105, i8 97, i8 32, i8 96, i8 96, i8 109, i8 101, i8 109, i8 111, i8 96, i8 96, i8 32, i8 115, i8 111, i8 10, i8 32, i8 32, i8 32, i8 32, i8 99, i8 121, i8 99, i8 108, i8 101, i8 115, i8 32, i8 100, i8 111, i8 110, i8 39, i8 116, i8 32, i8 98, i8 108, i8 111, i8 119, i8 32, i8 116, i8 104, i8 101, i8 32, i8 115, i8 116, i8 97, i8 99, i8 107, i8 46, i8 0]

@py_None = external constant ptr

@.cpy.attr.items = internal constant [6 x i8] [i8 105, i8 116, i8 101, i8 109, i8 115, i8 0]

@.pyattr.__deepcopy__ = internal constant [13 x i8] [i8 95, i8 95, i8 100, i8 101, i8 101, i8 112, i8 99, i8 111, i8 112, i8 121, i8 95, i8 95, i8 0]

@.cpy.attr.__deepcopy__ = internal constant [13 x i8] [i8 95, i8 95, i8 100, i8 101, i8 101, i8 112, i8 99, i8 111, i8 112, i8 121, i8 95, i8 95, i8 0]

@.pystr.3 = internal constant [53 x i8] [i8 112, i8 99, i8 99, i8 46, i8 112, i8 121, i8 95, i8 115, i8 116, i8 100, i8 108, i8 105, i8 98, i8 46, i8 99, i8 111, i8 112, i8 121, i8 32, i8 226, i8 128, i8 148, i8 32, i8 115, i8 104, i8 97, i8 108, i8 108, i8 111, i8 119, i8 32, i8 43, i8 32, i8 100, i8 101, i8 101, i8 112, i8 32, i8 99, i8 111, i8 112, i8 121, i8 32, i8 115, i8 107, i8 101, i8 108, i8 101, i8 116, i8 111, i8 110, i8 46, i8 0]

declare external void @py_incref(ptr)

declare external void @py_decref(ptr)

declare external ptr @py_bool_from_bit(i32)

declare external ptr @py_int_from_i64(i64)

declare external ptr @py_int_from_cstr(ptr, i32)

declare external i64 @py_int_to_i64(ptr, ptr)

declare external ptr @py_int_add(ptr, ptr)

declare external ptr @py_int_sub(ptr, ptr)

declare external ptr @py_int_mul(ptr, ptr)

declare external ptr @py_int_floordiv(ptr, ptr)

declare external ptr @py_int_truediv(ptr, ptr)

declare external ptr @py_int_mod(ptr, ptr)

declare external ptr @py_int_pow(ptr, ptr)

declare external ptr @py_int_neg(ptr)

declare external ptr @py_int_and(ptr, ptr)

declare external ptr @py_int_or(ptr, ptr)

declare external ptr @py_int_xor(ptr, ptr)

declare external ptr @py_int_shl(ptr, ptr)

declare external ptr @py_int_shr(ptr, ptr)

declare external i32 @py_int_cmp(ptr, ptr)

declare external ptr @py_float_from_f64(double)

declare external double @py_float_to_f64(ptr)

declare external ptr @py_float_add(ptr, ptr)

declare external ptr @py_str_new(ptr, i64)

declare external i64 @py_str_len(ptr)

declare external i64 @py_str_byte_len(ptr)

declare external ptr @py_str_utf8(ptr)

declare external ptr @py_str_concat(ptr, ptr)

declare external ptr @py_str_repeat(ptr, ptr)

declare external ptr @py_str_slice(ptr, ptr, ptr, ptr)

declare external ptr @py_str_index(ptr, ptr)

declare external i32 @py_str_eq(ptr, ptr)

declare external i32 @py_str_contains(ptr, ptr)

declare external i64 @py_str_find(ptr, ptr)

declare external ptr @py_str_upper(ptr)

declare external ptr @py_str_lower(ptr)

declare external ptr @py_str_strip(ptr)

declare external ptr @py_str_split(ptr, ptr)

declare external ptr @py_str_splitlines(ptr)

declare external ptr @py_str_splitlines_keepends(ptr, i32)

declare external ptr @py_str_lstrip(ptr)

declare external ptr @py_str_rstrip(ptr)

declare external ptr @py_str_strip_chars(ptr, ptr)

declare external ptr @py_str_lstrip_chars(ptr, ptr)

declare external ptr @py_str_rstrip_chars(ptr, ptr)

declare external i64 @py_str_count(ptr, ptr)

declare external i32 @py_str_isdigit(ptr)

declare external i32 @py_str_isalpha(ptr)

declare external i32 @py_str_isspace(ptr)

declare external i32 @py_str_isalnum(ptr)

declare external ptr @py_str_join(ptr, ptr)

declare external ptr @py_str_replace(ptr, ptr, ptr)

declare external i32 @py_str_startswith(ptr, ptr)

declare external i32 @py_str_endswith(ptr, ptr)

declare external ptr @py_list_new(i64)

declare external void @py_list_append(ptr, ptr)

declare external ptr @py_list_get(ptr, i64)

declare external void @py_list_set(ptr, i64, ptr)

declare external i64 @py_list_len(ptr)

declare external ptr @py_list_slice(ptr, ptr, ptr, ptr)

declare external ptr @py_list_concat(ptr, ptr)

declare external void @py_list_extend(ptr, ptr)

declare external void @py_list_insert(ptr, i64, ptr)

declare external ptr @py_list_pop(ptr, i64)

declare external void @py_list_remove(ptr, ptr)

declare external i32 @py_list_contains(ptr, ptr)

declare external i64 @py_list_index(ptr, ptr)

declare external ptr @py_dict_new()

declare external void @py_dict_set(ptr, ptr, ptr)

declare external ptr @py_dict_get(ptr, ptr)

declare external ptr @py_dict_get_default(ptr, ptr, ptr)

declare external i32 @py_dict_contains(ptr, ptr)

declare external i32 @py_dict_del(ptr, ptr)

declare external i64 @py_dict_len(ptr)

declare external ptr @py_dict_keys(ptr)

declare external ptr @py_dict_values(ptr)

declare external ptr @py_dict_items(ptr)

declare external ptr @py_tuple_new(i64)

declare external void @py_tuple_set_item(ptr, i64, ptr)

declare external ptr @py_tuple_get(ptr, i64)

declare external i64 @py_tuple_len(ptr)

declare external ptr @py_set_new()

declare external void @py_set_add(ptr, ptr)

declare external i32 @py_set_contains(ptr, ptr)

declare external i32 @py_set_remove(ptr, ptr)

declare external i64 @py_set_len(ptr)

declare external ptr @py_obj_call(ptr, ptr, ptr)

declare external ptr @py_obj_getattr(ptr, ptr)

declare external i32 @py_obj_setattr(ptr, ptr, ptr)

declare external ptr @py_obj_getitem(ptr, ptr)

declare external i32 @py_obj_setitem(ptr, ptr, ptr)

declare external i64 @py_obj_len(ptr)

declare external i32 @py_obj_contains(ptr, ptr)

declare external ptr @py_obj_sorted(ptr)

declare external i32 @py_obj_truthy(ptr)

declare external i32 @py_obj_eq(ptr, ptr)

declare external i64 @py_obj_hash(ptr)

declare external ptr @py_obj_repr(ptr)

declare external ptr @py_obj_str(ptr)

declare external i32 @py_obj_isinstance(ptr, ptr)

declare external void @py_print(ptr)

declare external void @py_print_many(ptr, ptr, ptr)

declare external void @py_raise(ptr)

declare external ptr @py_current_exception()

declare external void @py_clear_exception()

declare external ptr @py_exc_new(i32, ptr)

declare external ptr @py_exc_builtin_class(i32)

declare external i32 @py_exc_matches(ptr, ptr)

declare external void @py_exc_set_cause(ptr, ptr)

declare external void @py_exc_print_unhandled(ptr)

declare external ptr @py_exc_get_message(ptr)

declare external ptr @__cxa_begin_catch(ptr)

declare external void @__cxa_end_catch()

declare external ptr @py_class_new(ptr, ptr, i32, ptr, i32)

declare external void @py_class_add_method(ptr, ptr, ptr)

declare external ptr @py_class_lookup(ptr, ptr)

declare external ptr @py_instance_new(ptr)

declare external ptr @py_instance_get_field(ptr, i32)

declare external void @py_instance_set_field(ptr, i32, ptr)

declare external ptr @py_instance_getattr(ptr, ptr)

declare external i32 @py_instance_setattr(ptr, ptr, ptr)

declare external i32 @py_isinstance(ptr, ptr)

declare external ptr @py_super_lookup(ptr, ptr, ptr)

declare external void @py_gc_init()

declare external void @py_gc_collect()

declare external void @py_gc_track(ptr)

declare external void @py_gc_untrack(ptr)

declare external void @py_cpy_ensure_init()

declare external ptr @py_cpy_import(ptr)

declare external ptr @py_cpy_getattr(ptr, ptr)

declare external ptr @py_cpy_call_noargs(ptr)

declare external ptr @py_cpy_call1(ptr, ptr)

declare external ptr @py_cpy_call2(ptr, ptr, ptr)

declare external ptr @py_cpy_call3(ptr, ptr, ptr, ptr)

declare external ptr @py_cpy_call_argv(ptr, i64, ptr)

declare external ptr @py_cpy_call_kw(ptr, i64, ptr, i64, ptr, ptr)

declare external i64 @py_cpy_len(ptr)

declare external ptr @py_cpy_getitem(ptr, ptr)

declare external i32 @py_cpy_setitem(ptr, ptr, ptr)

declare external i32 @py_cpy_truthy(ptr)

declare external ptr @py_cpy_iter(ptr)

declare external ptr @py_cpy_iter_next(ptr)

declare external ptr @py_cpy_to_pcc_str(ptr)

declare external void @py_cpy_decref(ptr)

declare external ptr @py_cpy_from_i64(i64)

declare external i64 @py_cpy_to_i64(ptr)

declare external ptr @py_cpy_from_f64(double)

declare external double @py_cpy_to_f64(ptr)

declare external ptr @py_cpy_from_pccstr(ptr)

declare external ptr @py_cpy_from_pcc_obj(ptr)

declare external i32 @printf(ptr, ...)

define external ptr @user_copy_copy(ptr %x) {
entry:
  %x.addr = alloca ptr
  %list.idx.addr = alloca i64
  %dict.copy.idx.addr = alloca i64
  %tuple.idx.addr = alloca i64
  %set.idx.addr = alloca i64
  store ptr %x, ptr %x.addr
  %pystr.ptr.1 = getelementptr inbounds [14 x i8], ptr @.pystr.1, i32 0, i32 0
  %str.new.2 = call ptr (ptr, i64) @py_str_new(ptr %pystr.ptr.1, i64 13)
  %x.3 = load ptr, ptr %x.addr
  %m.dyn.b2i32 = zext i1 0 to i32
  %m.dyn.bool_box = call ptr (i32) @py_bool_from_bit(i32 %m.dyn.b2i32)
  %truthy_obj.4 = call i32 (ptr) @py_obj_truthy(ptr %m.dyn.bool_box)
  %truthy_obj_i1.5 = trunc i32 %truthy_obj.4 to i1
  br i1 %truthy_obj_i1.5, label %if.then.6, label %if.else.7

if.then.6:
  %list.new.9 = call ptr (i64) @py_list_new(i64 0)
  %x.10 = load ptr, ptr %x.addr
  %list.src.len.11 = call i64 (ptr) @py_obj_len(ptr %x.10)
  store i64 0, ptr %list.idx.addr
  br label %list.cond.12

if.else.7:
  br label %if.end.8

if.end.8:
  %x.21 = load ptr, ptr %x.addr
  %m.dyn.b2i32.1 = zext i1 0 to i32
  %m.dyn.bool_box.1 = call ptr (i32) @py_bool_from_bit(i32 %m.dyn.b2i32.1)
  %truthy_obj.22 = call i32 (ptr) @py_obj_truthy(ptr %m.dyn.bool_box.1)
  %truthy_obj_i1.23 = trunc i32 %truthy_obj.22 to i1
  br i1 %truthy_obj_i1.23, label %if.then.24, label %if.else.25

list.cond.12:
  %list.idx.16 = load i64, ptr %list.idx.addr
  %list.cond.i1.17 = icmp slt i64 %list.idx.16, %list.src.len.11
  br i1 %list.cond.i1.17, label %list.body.13, label %list.end.15

list.body.13:
  %list.idx.box.18 = call ptr (i64) @py_int_from_i64(i64 %list.idx.16)
  %list.elem.19 = call ptr (ptr, ptr) @py_obj_getitem(ptr %x.10, ptr %list.idx.box.18)
  call void (ptr, ptr) @py_list_append(ptr %list.new.9, ptr %list.elem.19)
  br label %list.step.14

list.step.14:
  %list.idx.next.20 = add i64 %list.idx.16, 1
  store i64 %list.idx.next.20, ptr %list.idx.addr
  br label %list.cond.12

list.end.15:
  ret ptr %list.new.9

if.then.24:
  %dict.new.27 = call ptr () @py_dict_new()
  %x.28 = load ptr, ptr %x.addr
  %dict.copy.keys.29 = call ptr (ptr) @py_dict_keys(ptr %x.28)
  %dict.copy.len.30 = call i64 (ptr) @py_obj_len(ptr %dict.copy.keys.29)
  store i64 0, ptr %dict.copy.idx.addr
  br label %dict.copy.cond.31

if.else.25:
  br label %if.end.26

if.end.26:
  %x.40 = load ptr, ptr %x.addr
  %m.dyn.b2i32.2 = zext i1 0 to i32
  %m.dyn.bool_box.2 = call ptr (i32) @py_bool_from_bit(i32 %m.dyn.b2i32.2)
  %truthy_obj.41 = call i32 (ptr) @py_obj_truthy(ptr %m.dyn.bool_box.2)
  %truthy_obj_i1.42 = trunc i32 %truthy_obj.41 to i1
  br i1 %truthy_obj_i1.42, label %if.then.43, label %if.else.44

dict.copy.cond.31:
  %idx.35 = load i64, ptr %dict.copy.idx.addr
  %cond.i1.36 = icmp slt i64 %idx.35, %dict.copy.len.30
  br i1 %cond.i1.36, label %dict.copy.body.32, label %dict.copy.end.34

dict.copy.body.32:
  %dict.copy.key.37 = call ptr (ptr, i64) @py_list_get(ptr %dict.copy.keys.29, i64 %idx.35)
  %dict.copy.val.38 = call ptr (ptr, ptr) @py_dict_get(ptr %x.28, ptr %dict.copy.key.37)
  call void (ptr, ptr, ptr) @py_dict_set(ptr %dict.new.27, ptr %dict.copy.key.37, ptr %dict.copy.val.38)
  br label %dict.copy.step.33

dict.copy.step.33:
  %idx.next.39 = add i64 %idx.35, 1
  store i64 %idx.next.39, ptr %dict.copy.idx.addr
  br label %dict.copy.cond.31

dict.copy.end.34:
  ret ptr %dict.new.27

if.then.43:
  %x.46 = load ptr, ptr %x.addr
  %tuple.src.len.47 = call i64 (ptr) @py_obj_len(ptr %x.46)
  %tuple.new.48 = call ptr (i64) @py_tuple_new(i64 %tuple.src.len.47)
  store i64 0, ptr %tuple.idx.addr
  br label %tuple.cond.49

if.else.44:
  br label %if.end.45

if.end.45:
  %x.58 = load ptr, ptr %x.addr
  %m.dyn.b2i32.3 = zext i1 0 to i32
  %m.dyn.bool_box.3 = call ptr (i32) @py_bool_from_bit(i32 %m.dyn.b2i32.3)
  %truthy_obj.59 = call i32 (ptr) @py_obj_truthy(ptr %m.dyn.bool_box.3)
  %truthy_obj_i1.60 = trunc i32 %truthy_obj.59 to i1
  br i1 %truthy_obj_i1.60, label %if.then.61, label %if.else.62

tuple.cond.49:
  %tuple.idx.53 = load i64, ptr %tuple.idx.addr
  %tuple.cond.i1.54 = icmp slt i64 %tuple.idx.53, %tuple.src.len.47
  br i1 %tuple.cond.i1.54, label %tuple.body.50, label %tuple.end.52

tuple.body.50:
  %tuple.idx.box.55 = call ptr (i64) @py_int_from_i64(i64 %tuple.idx.53)
  %tuple.elem.56 = call ptr (ptr, ptr) @py_obj_getitem(ptr %x.46, ptr %tuple.idx.box.55)
  call void (ptr, i64, ptr) @py_tuple_set_item(ptr %tuple.new.48, i64 %tuple.idx.53, ptr %tuple.elem.56)
  br label %tuple.step.51

tuple.step.51:
  %tuple.idx.next.57 = add i64 %tuple.idx.53, 1
  store i64 %tuple.idx.next.57, ptr %tuple.idx.addr
  br label %tuple.cond.49

tuple.end.52:
  ret ptr %tuple.new.48

if.then.61:
  %set.new.64 = call ptr () @py_set_new()
  %x.65 = load ptr, ptr %x.addr
  %set.src.len.66 = call i64 (ptr) @py_obj_len(ptr %x.65)
  store i64 0, ptr %set.idx.addr
  br label %set.cond.67

if.else.62:
  br label %if.end.63

if.end.63:
  %x.76 = load ptr, ptr %x.addr
  %.2 = getelementptr inbounds [9 x i8], ptr @.pyattr.__copy__, i32 0, i32 0
  %hasattr.got.77 = call ptr (ptr, ptr) @py_obj_getattr(ptr %x.76, ptr %.2)
  %hasattr.i1.78 = icmp ne ptr %hasattr.got.77, null
  %m.dyn.b2i32.4 = zext i1 %hasattr.i1.78 to i32
  %m.dyn.bool_box.4 = call ptr (i32) @py_bool_from_bit(i32 %m.dyn.b2i32.4)
  %truthy_obj.79 = call i32 (ptr) @py_obj_truthy(ptr %m.dyn.bool_box.4)
  %truthy_obj_i1.80 = trunc i32 %truthy_obj.79 to i1
  br i1 %truthy_obj_i1.80, label %if.then.81, label %if.else.82

set.cond.67:
  %set.idx.71 = load i64, ptr %set.idx.addr
  %set.cond.i1.72 = icmp slt i64 %set.idx.71, %set.src.len.66
  br i1 %set.cond.i1.72, label %set.body.68, label %set.end.70

set.body.68:
  %set.idx.box.73 = call ptr (i64) @py_int_from_i64(i64 %set.idx.71)
  %set.elem.74 = call ptr (ptr, ptr) @py_obj_getitem(ptr %x.65, ptr %set.idx.box.73)
  call void (ptr, ptr) @py_set_add(ptr %set.new.64, ptr %set.elem.74)
  br label %set.step.69

set.step.69:
  %set.idx.next.75 = add i64 %set.idx.71, 1
  store i64 %set.idx.next.75, ptr %set.idx.addr
  br label %set.cond.67

set.end.70:
  ret ptr %set.new.64

if.then.81:
  %x.84 = load ptr, ptr %x.addr
  %.3 = getelementptr inbounds [9 x i8], ptr @.cpy.attr.__copy__, i32 0, i32 0
  %cpy.fn.__copy__.85 = call ptr (ptr, ptr) @py_cpy_getattr(ptr %x.84, ptr %.3)
  %cpy.call0.__copy__.86 = call ptr (ptr) @py_cpy_call_noargs(ptr %cpy.fn.__copy__.85)
  call void (ptr) @py_cpy_decref(ptr %cpy.fn.__copy__.85)
  ret ptr %cpy.call0.__copy__.86

if.else.82:
  br label %if.end.83

if.end.83:
  %x.87 = load ptr, ptr %x.addr
  ret ptr %x.87
}

define external ptr @user_copy_deepcopy(ptr %x, ptr %memo) {
entry:
  %x.addr = alloca ptr
  %oid.addr = alloca i64
  %out.addr = alloca ptr
  %for.obj.idx.addr = alloca i64
  %item.addr = alloca ptr
  %comp.obj.idx.addr = alloca i64
  %item.addr.1 = alloca ptr
  %tuple.idx.addr = alloca i64
  %out_d.addr = alloca ptr
  %foritem.181.addr = alloca ptr
  %k.addr = alloca ptr
  %v.addr = alloca ptr
  %comp.obj.idx.addr.1 = alloca i64
  %item.addr.2 = alloca ptr
  %set.idx.addr = alloca i64
  %r.addr = alloca ptr
  store ptr %x, ptr %x.addr
  %memo.addr = alloca ptr
  store ptr %memo, ptr %memo.addr
  %pystr.ptr.88 = getelementptr inbounds [165 x i8], ptr @.pystr.2, i32 0, i32 0
  %str.new.89 = call ptr (ptr, i64) @py_str_new(ptr %pystr.ptr.88, i64 164)
  %memo.90 = load ptr, ptr %memo.addr
  %none.91 = load ptr, ptr @py_None
  %is.l.92 = ptrtoint ptr %memo.90 to i64
  %is.r.93 = ptrtoint ptr %none.91 to i64
  %is.94 = icmp eq i64 %is.l.92, %is.r.93
  br i1 %is.94, label %if.then.95, label %if.else.96

if.then.95:
  %dict.new.98 = call ptr () @py_dict_new()
  store ptr %dict.new.98, ptr %memo.addr
  br label %if.end.97

if.else.96:
  br label %if.end.97

if.end.97:
  %x.99 = load ptr, ptr %x.addr
  %id.100 = ptrtoint ptr %x.99 to i64
  store i64 %id.100, ptr %oid.addr
  %oid.101 = load i64, ptr %oid.addr
  %memo.102 = load ptr, ptr %memo.addr
  %m.int_box = call ptr (i64) @py_int_from_i64(i64 %oid.101)
  %dict.in.103 = call i32 (ptr, ptr) @py_dict_contains(ptr %memo.102, ptr %m.int_box)
  %in.i1.104 = icmp ne i32 %dict.in.103, 0
  br i1 %in.i1.104, label %if.then.105, label %if.else.106

if.then.105:
  %memo.108 = load ptr, ptr %memo.addr
  %oid.109 = load i64, ptr %oid.addr
  %m.int_box.1 = call ptr (i64) @py_int_from_i64(i64 %oid.109)
  %dict.get.110 = call ptr (ptr, ptr) @py_dict_get(ptr %memo.108, ptr %m.int_box.1)
  ret ptr %dict.get.110

if.else.106:
  br label %if.end.107

if.end.107:
  %x.111 = load ptr, ptr %x.addr
  %m.dyn.b2i32 = zext i1 0 to i32
  %m.dyn.bool_box = call ptr (i32) @py_bool_from_bit(i32 %m.dyn.b2i32)
  %truthy_obj.112 = call i32 (ptr) @py_obj_truthy(ptr %m.dyn.bool_box)
  %truthy_obj_i1.113 = trunc i32 %truthy_obj.112 to i1
  br i1 %truthy_obj_i1.113, label %if.then.114, label %if.else.115

if.then.114:
  %list.new.117 = call ptr (i64) @py_list_new(i64 0)
  store ptr %list.new.117, ptr %out.addr
  %memo.118 = load ptr, ptr %memo.addr
  %out.119 = load ptr, ptr %out.addr
  %oid.120 = load i64, ptr %oid.addr
  %m.int_box.2 = call ptr (i64) @py_int_from_i64(i64 %oid.120)
  call void (ptr, ptr, ptr) @py_dict_set(ptr %memo.118, ptr %m.int_box.2, ptr %out.119)
  %x.121 = load ptr, ptr %x.addr
  %for.obj.len.122 = call i64 (ptr) @py_obj_len(ptr %x.121)
  store i64 0, ptr %for.obj.idx.addr
  br label %for.obj.cond.123

if.else.115:
  br label %if.end.116

if.end.116:
  %x.138 = load ptr, ptr %x.addr
  %m.dyn.b2i32.1 = zext i1 0 to i32
  %m.dyn.bool_box.1 = call ptr (i32) @py_bool_from_bit(i32 %m.dyn.b2i32.1)
  %truthy_obj.139 = call i32 (ptr) @py_obj_truthy(ptr %m.dyn.bool_box.1)
  %truthy_obj_i1.140 = trunc i32 %truthy_obj.139 to i1
  br i1 %truthy_obj_i1.140, label %if.then.141, label %if.else.142

for.obj.cond.123:
  %for.obj.idx.127 = load i64, ptr %for.obj.idx.addr
  %for.obj.cond.i1.128 = icmp slt i64 %for.obj.idx.127, %for.obj.len.122
  br i1 %for.obj.cond.i1.128, label %for.obj.body.124, label %for.obj.end.126

for.obj.body.124:
  %for.obj.idx.box.129 = call ptr (i64) @py_int_from_i64(i64 %for.obj.idx.127)
  %for.obj.elem.130 = call ptr (ptr, ptr) @py_obj_getitem(ptr %x.121, ptr %for.obj.idx.box.129)
  store ptr %for.obj.elem.130, ptr %item.addr
  %out.131 = load ptr, ptr %out.addr
  %item.132 = load ptr, ptr %item.addr
  %memo.133 = load ptr, ptr %memo.addr
  %deepcopy_ret.134 = call ptr (ptr, ptr) @user_copy_deepcopy(ptr %item.132, ptr %memo.133)
  call void (ptr, ptr) @py_list_append(ptr %out.131, ptr %deepcopy_ret.134)
  br label %for.obj.step.125

for.obj.step.125:
  %for.obj.idx2.135 = load i64, ptr %for.obj.idx.addr
  %for.obj.next.136 = add i64 %for.obj.idx2.135, 1
  store i64 %for.obj.next.136, ptr %for.obj.idx.addr
  br label %for.obj.cond.123

for.obj.end.126:
  %out.137 = load ptr, ptr %out.addr
  ret ptr %out.137

if.then.141:
  %listcomp.144 = call ptr (i64) @py_list_new(i64 0)
  %x.145 = load ptr, ptr %x.addr
  %comp.obj.len.146 = call i64 (ptr) @py_obj_len(ptr %x.145)
  store i64 0, ptr %comp.obj.idx.addr
  br label %comp.obj.cond.147

if.else.142:
  br label %if.end.143

if.end.143:
  %x.171 = load ptr, ptr %x.addr
  %m.dyn.b2i32.2 = zext i1 0 to i32
  %m.dyn.bool_box.2 = call ptr (i32) @py_bool_from_bit(i32 %m.dyn.b2i32.2)
  %truthy_obj.172 = call i32 (ptr) @py_obj_truthy(ptr %m.dyn.bool_box.2)
  %truthy_obj_i1.173 = trunc i32 %truthy_obj.172 to i1
  br i1 %truthy_obj_i1.173, label %if.then.174, label %if.else.175

comp.obj.cond.147:
  %comp.obj.idx.151 = load i64, ptr %comp.obj.idx.addr
  %comp.obj.cond.152 = icmp slt i64 %comp.obj.idx.151, %comp.obj.len.146
  br i1 %comp.obj.cond.152, label %comp.obj.body.148, label %comp.obj.end.150

comp.obj.body.148:
  %comp.obj.idx.box.153 = call ptr (i64) @py_int_from_i64(i64 %comp.obj.idx.151)
  %comp.obj.elem.154 = call ptr (ptr, ptr) @py_obj_getitem(ptr %x.145, ptr %comp.obj.idx.box.153)
  store ptr %comp.obj.elem.154, ptr %item.addr.1
  %item.155 = load ptr, ptr %item.addr.1
  %memo.156 = load ptr, ptr %memo.addr
  %deepcopy_ret.157 = call ptr (ptr, ptr) @user_copy_deepcopy(ptr %item.155, ptr %memo.156)
  call void (ptr, ptr) @py_list_append(ptr %listcomp.144, ptr %deepcopy_ret.157)
  br label %comp.obj.step.149

comp.obj.step.149:
  %comp.obj.idx2.158 = load i64, ptr %comp.obj.idx.addr
  %comp.obj.idx.next.159 = add i64 %comp.obj.idx2.158, 1
  store i64 %comp.obj.idx.next.159, ptr %comp.obj.idx.addr
  br label %comp.obj.cond.147

comp.obj.end.150:
  %tuple.src.len.160 = call i64 (ptr) @py_obj_len(ptr %listcomp.144)
  %tuple.new.161 = call ptr (i64) @py_tuple_new(i64 %tuple.src.len.160)
  store i64 0, ptr %tuple.idx.addr
  br label %tuple.cond.162

tuple.cond.162:
  %tuple.idx.166 = load i64, ptr %tuple.idx.addr
  %tuple.cond.i1.167 = icmp slt i64 %tuple.idx.166, %tuple.src.len.160
  br i1 %tuple.cond.i1.167, label %tuple.body.163, label %tuple.end.165

tuple.body.163:
  %tuple.idx.box.168 = call ptr (i64) @py_int_from_i64(i64 %tuple.idx.166)
  %tuple.elem.169 = call ptr (ptr, ptr) @py_obj_getitem(ptr %listcomp.144, ptr %tuple.idx.box.168)
  call void (ptr, i64, ptr) @py_tuple_set_item(ptr %tuple.new.161, i64 %tuple.idx.166, ptr %tuple.elem.169)
  br label %tuple.step.164

tuple.step.164:
  %tuple.idx.next.170 = add i64 %tuple.idx.166, 1
  store i64 %tuple.idx.next.170, ptr %tuple.idx.addr
  br label %tuple.cond.162

tuple.end.165:
  ret ptr %tuple.new.161

if.then.174:
  %dict.new.177 = call ptr () @py_dict_new()
  store ptr %dict.new.177, ptr %out_d.addr
  %memo.178 = load ptr, ptr %memo.addr
  %out_d.179 = load ptr, ptr %out_d.addr
  %oid.180 = load i64, ptr %oid.addr
  %m.int_box.3 = call ptr (i64) @py_int_from_i64(i64 %oid.180)
  call void (ptr, ptr, ptr) @py_dict_set(ptr %memo.178, ptr %m.int_box.3, ptr %out_d.179)
  %x.182 = load ptr, ptr %x.addr
  %.3 = getelementptr inbounds [6 x i8], ptr @.cpy.attr.items, i32 0, i32 0
  %cpy.fn.items.183 = call ptr (ptr, ptr) @py_cpy_getattr(ptr %x.182, ptr %.3)
  %cpy.call0.items.184 = call ptr (ptr) @py_cpy_call_noargs(ptr %cpy.fn.items.183)
  call void (ptr) @py_cpy_decref(ptr %cpy.fn.items.183)
  %cpy.iter.185 = call ptr (ptr) @py_cpy_iter(ptr %cpy.call0.items.184)
  br label %for.cpy.header.186

if.else.175:
  br label %if.end.176

if.end.176:
  %x.204 = load ptr, ptr %x.addr
  %m.dyn.b2i32.3 = zext i1 0 to i32
  %m.dyn.bool_box.3 = call ptr (i32) @py_bool_from_bit(i32 %m.dyn.b2i32.3)
  %truthy_obj.205 = call i32 (ptr) @py_obj_truthy(ptr %m.dyn.bool_box.3)
  %truthy_obj_i1.206 = trunc i32 %truthy_obj.205 to i1
  br i1 %truthy_obj_i1.206, label %if.then.207, label %if.else.208

for.cpy.header.186:
  %cpy.next.189 = call ptr (ptr) @py_cpy_iter_next(ptr %cpy.iter.185)
  %cpy.next.isnull.190 = icmp eq ptr %cpy.next.189, null
  br i1 %cpy.next.isnull.190, label %for.cpy.after.188, label %for.cpy.body.187

for.cpy.body.187:
  store ptr %cpy.next.189, ptr %foritem.181.addr
  %foritem.181.191 = load ptr, ptr %foritem.181.addr
  %unpack.idx.box.192 = call ptr (i64) @py_int_from_i64(i64 0)
  %unpack.0.193 = call ptr (ptr, ptr) @py_obj_getitem(ptr %foritem.181.191, ptr %unpack.idx.box.192)
  store ptr %unpack.0.193, ptr %k.addr
  %unpack.idx.box.194 = call ptr (i64) @py_int_from_i64(i64 1)
  %unpack.1.195 = call ptr (ptr, ptr) @py_obj_getitem(ptr %foritem.181.191, ptr %unpack.idx.box.194)
  store ptr %unpack.1.195, ptr %v.addr
  %out_d.196 = load ptr, ptr %out_d.addr
  %v.197 = load ptr, ptr %v.addr
  %memo.198 = load ptr, ptr %memo.addr
  %deepcopy_ret.199 = call ptr (ptr, ptr) @user_copy_deepcopy(ptr %v.197, ptr %memo.198)
  %k.200 = load ptr, ptr %k.addr
  %memo.201 = load ptr, ptr %memo.addr
  %deepcopy_ret.202 = call ptr (ptr, ptr) @user_copy_deepcopy(ptr %k.200, ptr %memo.201)
  call void (ptr, ptr, ptr) @py_dict_set(ptr %out_d.196, ptr %deepcopy_ret.202, ptr %deepcopy_ret.199)
  br label %for.cpy.header.186

for.cpy.after.188:
  call void (ptr) @py_cpy_decref(ptr %cpy.iter.185)
  %out_d.203 = load ptr, ptr %out_d.addr
  ret ptr %out_d.203

if.then.207:
  %set.new.210 = call ptr () @py_set_new()
  %listcomp.211 = call ptr (i64) @py_list_new(i64 0)
  %x.212 = load ptr, ptr %x.addr
  %comp.obj.len.213 = call i64 (ptr) @py_obj_len(ptr %x.212)
  store i64 0, ptr %comp.obj.idx.addr.1
  br label %comp.obj.cond.214

if.else.208:
  br label %if.end.209

if.end.209:
  %x.237 = load ptr, ptr %x.addr
  %.4 = getelementptr inbounds [13 x i8], ptr @.pyattr.__deepcopy__, i32 0, i32 0
  %hasattr.got.238 = call ptr (ptr, ptr) @py_obj_getattr(ptr %x.237, ptr %.4)
  %hasattr.i1.239 = icmp ne ptr %hasattr.got.238, null
  %m.dyn.b2i32.4 = zext i1 %hasattr.i1.239 to i32
  %m.dyn.bool_box.4 = call ptr (i32) @py_bool_from_bit(i32 %m.dyn.b2i32.4)
  %truthy_obj.240 = call i32 (ptr) @py_obj_truthy(ptr %m.dyn.bool_box.4)
  %truthy_obj_i1.241 = trunc i32 %truthy_obj.240 to i1
  br i1 %truthy_obj_i1.241, label %if.then.242, label %if.else.243

comp.obj.cond.214:
  %comp.obj.idx.218 = load i64, ptr %comp.obj.idx.addr.1
  %comp.obj.cond.219 = icmp slt i64 %comp.obj.idx.218, %comp.obj.len.213
  br i1 %comp.obj.cond.219, label %comp.obj.body.215, label %comp.obj.end.217

comp.obj.body.215:
  %comp.obj.idx.box.220 = call ptr (i64) @py_int_from_i64(i64 %comp.obj.idx.218)
  %comp.obj.elem.221 = call ptr (ptr, ptr) @py_obj_getitem(ptr %x.212, ptr %comp.obj.idx.box.220)
  store ptr %comp.obj.elem.221, ptr %item.addr.2
  %item.222 = load ptr, ptr %item.addr.2
  %memo.223 = load ptr, ptr %memo.addr
  %deepcopy_ret.224 = call ptr (ptr, ptr) @user_copy_deepcopy(ptr %item.222, ptr %memo.223)
  call void (ptr, ptr) @py_list_append(ptr %listcomp.211, ptr %deepcopy_ret.224)
  br label %comp.obj.step.216

comp.obj.step.216:
  %comp.obj.idx2.225 = load i64, ptr %comp.obj.idx.addr.1
  %comp.obj.idx.next.226 = add i64 %comp.obj.idx2.225, 1
  store i64 %comp.obj.idx.next.226, ptr %comp.obj.idx.addr.1
  br label %comp.obj.cond.214

comp.obj.end.217:
  %set.src.len.227 = call i64 (ptr) @py_obj_len(ptr %listcomp.211)
  store i64 0, ptr %set.idx.addr
  br label %set.cond.228

set.cond.228:
  %set.idx.232 = load i64, ptr %set.idx.addr
  %set.cond.i1.233 = icmp slt i64 %set.idx.232, %set.src.len.227
  br i1 %set.cond.i1.233, label %set.body.229, label %set.end.231

set.body.229:
  %set.idx.box.234 = call ptr (i64) @py_int_from_i64(i64 %set.idx.232)
  %set.elem.235 = call ptr (ptr, ptr) @py_obj_getitem(ptr %listcomp.211, ptr %set.idx.box.234)
  call void (ptr, ptr) @py_set_add(ptr %set.new.210, ptr %set.elem.235)
  br label %set.step.230

set.step.230:
  %set.idx.next.236 = add i64 %set.idx.232, 1
  store i64 %set.idx.next.236, ptr %set.idx.addr
  br label %set.cond.228

set.end.231:
  ret ptr %set.new.210

if.then.242:
  %x.245 = load ptr, ptr %x.addr
  %.5 = getelementptr inbounds [13 x i8], ptr @.cpy.attr.__deepcopy__, i32 0, i32 0
  %cpy.fn.__deepcopy__.246 = call ptr (ptr, ptr) @py_cpy_getattr(ptr %x.245, ptr %.5)
  %memo.247 = load ptr, ptr %memo.addr
  %cpy.from_pcc_dict.248 = call ptr (ptr) @py_cpy_from_pcc_obj(ptr %memo.247)
  %cpy.call1.__deepcopy__.249 = call ptr (ptr, ptr) @py_cpy_call1(ptr %cpy.fn.__deepcopy__.246, ptr %cpy.from_pcc_dict.248)
  call void (ptr) @py_cpy_decref(ptr %cpy.from_pcc_dict.248)
  call void (ptr) @py_cpy_decref(ptr %cpy.fn.__deepcopy__.246)
  store ptr %cpy.call1.__deepcopy__.249, ptr %r.addr
  %memo.250 = load ptr, ptr %memo.addr
  %r.251 = load ptr, ptr %r.addr
  %oid.252 = load i64, ptr %oid.addr
  %m.int_box.4 = call ptr (i64) @py_int_from_i64(i64 %oid.252)
  call void (ptr, ptr, ptr) @py_dict_set(ptr %memo.250, ptr %m.int_box.4, ptr %r.251)
  %r.253 = load ptr, ptr %r.addr
  ret ptr %r.253

if.else.243:
  br label %if.end.244

if.end.244:
  %x.254 = load ptr, ptr %x.addr
  ret ptr %x.254
}

define i32 @main() {
entry:
  %pystr.ptr.255 = getelementptr inbounds [53 x i8], ptr @.pystr.3, i32 0, i32 0
  %str.new.256 = call ptr (ptr, i64) @py_str_new(ptr %pystr.ptr.255, i64 52)
  ret i32 0
}
