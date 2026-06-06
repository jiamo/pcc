/*
 * Reusable pcc NumPy C-API provider.
 *
 * This remains one compiled translation unit because provider consumers and
 * package-build smokes compile pccnpapi.c as the public source entrypoint.
 * The implementation lives in routed include shards so new C-API slices land
 * near related behavior instead of growing an 11k-line single file.
 */

#include "pccnpapi_impl/pccnpapi_core.inc"
#include "pccnpapi_impl/pccnpapi_coercion.inc"
#include "pccnpapi_impl/pccnpapi_converters.inc"
#include "pccnpapi_impl/pccnpapi_shape_iter.inc"
#include "pccnpapi_impl/pccnpapi_indexing_ops.inc"
#include "pccnpapi_impl/pccnpapi_reduction_ops.inc"
#include "pccnpapi_impl/pccnpapi_ufunc_module.inc"
