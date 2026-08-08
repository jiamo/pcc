"""Metal render-surface bridge for the pcc GUI (2D offscreen render).

This is the RENDER side of Metal, distinct from the existing COMPUTE side
(``metal_source_runtime`` executes kernel_ir compute kernels).  It emits an
Objective-C bridge that:

1. creates a Metal device + an offscreen ``MTLTexture`` (RGBA8),
2. builds a render pipeline from embedded MSL (pass-through 2D vertex
   shader + solid-color fragment shader),
3. draws a list of solid-color rectangles (the pcc_gui element records:
   32-byte rects + 4-byte colors),
4. reads the texture back into a caller buffer (``getBytes``).

The result is a native pixel buffer that pcc_gui can blit or hand to a
window backend — a local-machine, hardware-gated proof of the Metal render
surface.  It does not yet own a CAMetalLayer / present path (window
integration) or text/gradient shaders; those are later slices of the same
bridge.

The bridge is emitted as an Objective-C source string, compiled by the host
toolchain into a dylib, and invoked through dlopen — the same mechanism as
``metal_source_runtime``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

RENDER_BRIDGE_SYMBOL = "pcc_gui_metal_render_offscreen"
RENDER_BRIDGE_LAST_ERROR = "pcc_gui_metal_render_copy_last_error"
WINDOW_CREATE_SYMBOL = "pcc_gui_metal_window_create"
WINDOW_RENDER_SYMBOL = "pcc_gui_metal_window_render"
WINDOW_SHOW_SYMBOL = "pcc_gui_metal_window_show"
WINDOW_CLOSE_SYMBOL = "pcc_gui_metal_window_close"
WINDOW_PUMP_SYMBOL = "pcc_gui_metal_run_loop_pump"
WINDOW_CLOSED_SYMBOL = "pcc_gui_metal_window_is_closed"
WINDOW_POLL_CLICK_SYMBOL = "pcc_gui_metal_window_poll_click"
WINDOW_TEXT_SYMBOL = "pcc_gui_metal_window_text"
WINDOW_SIZE_SYMBOL = "pcc_gui_metal_window_size"
OPEN_PANEL_SYMBOL = "pcc_gui_metal_open_panel"
WINDOW_CAPTURE_SYMBOL = "pcc_gui_metal_window_capture"
WINDOW_LIFECYCLE_INSTALL_SYMBOL = "pcc_gui_metal_lifecycle_install"
WINDOW_LIFECYCLE_PROBE_SYMBOL = "pcc_gui_metal_lifecycle_probe"

_MSL = r"""
#include <metal_stdlib>
using namespace metal;

struct VOut {
    float4 position [[position]];
    float4 color;
};

vertex VOut pcc_rect_vs(uint vid [[vertex_id]],
                        const device float2 *pos [[buffer(0)]],
                        const device float4 *col [[buffer(1)]],
                        constant float2 &size [[buffer(2)]]) {
    VOut out;
    float2 p = pos[vid];
    float2 ndc = float2(p.x / size.x, 1.0 - p.y / size.y) * 2.0 - 1.0;
    out.position = float4(ndc, 0.0, 1.0);
    out.color = col[vid];
    return out;
}

fragment float4 pcc_rect_fs(VOut in [[stage_in]]) {
    return in.color;
}

struct TOut {
    float4 position [[position]];
    float2 uv;
};

vertex TOut pcc_tex_vs(uint vid [[vertex_id]]) {
    float2 pos[4] = { float2(-1,-1), float2(1,-1), float2(-1,1), float2(1,1) };
    float2 uv[4] = { float2(0,1), float2(1,1), float2(0,0), float2(1,0) };
    TOut out;
    out.position = float4(pos[vid], 0.0, 1.0);
    out.uv = uv[vid];
    return out;
}

fragment float4 pcc_tex_fs(TOut in [[stage_in]],
                           texture2d<float> tex [[texture(0)]],
                           sampler smp [[sampler(0)]]) {
    return tex.sample(smp, in.uv);
}
"""


def metal_render_bridge_source() -> str:
    """Emit the Objective-C render bridge source as a string."""
    msl = _MSL.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f"""\
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <QuartzCore/QuartzCore.h>
#import <AppKit/AppKit.h>
#include <stdint.h>
#include <string.h>

static char pcc_render_last_error[512];
static uint64_t pcc_last_click = 0;  /* 0 = none, else packed (y<<32)|x */
static CATextLayer *pcc_text_layers[512];
static NSTextField *pcc_text_fields[512];
static CAMetalLayer *pcc_metal_layer = nil;
static NSView *pcc_metal_view = nil;

typedef int32_t (*PccGuiLifecycleSink)(int32_t kind, uint64_t window_id,
    const void *payload, uint64_t payload_length, uint32_t flags,
    int32_t exit_code);

enum {{
  PCC_GUI_APP_WINDOW_EVENT = 4,
  PCC_GUI_APP_OPENED = 5,
  PCC_GUI_APP_REOPEN = 6,
  PCC_GUI_APP_EXIT_REQUESTED = 7,
}};

typedef struct {{
  int32_t kind;
  int32_t flags;
  int64_t width;
  int64_t height;
  int64_t native_type;
}} PccGuiNativeWindowEventV1;

static PccGuiLifecycleSink pcc_lifecycle_sink = NULL;
static uint64_t pcc_lifecycle_window_id = 0;

@interface PccGuiLifecycleDelegate : NSObject <NSApplicationDelegate, NSWindowDelegate>
@end

@implementation PccGuiLifecycleDelegate
- (void)windowDidResize:(NSNotification *)notification {{
  if (pcc_lifecycle_sink == NULL) return;
  NSWindow *window = (NSWindow *)[notification object];
  NSRect bounds = [[window contentView] bounds];
  PccGuiNativeWindowEventV1 event = {{
    1, 0, (int64_t)bounds.size.width, (int64_t)bounds.size.height, 0
  }};
  pcc_lifecycle_sink(PCC_GUI_APP_WINDOW_EVENT, pcc_lifecycle_window_id,
                     &event, sizeof(event), 0, 0);
}}

- (void)windowDidBecomeKey:(NSNotification *)notification {{
  if (pcc_lifecycle_sink == NULL) return;
  PccGuiNativeWindowEventV1 event = {{2, 0, 0, 0, 0}};
  pcc_lifecycle_sink(PCC_GUI_APP_WINDOW_EVENT, pcc_lifecycle_window_id,
                     &event, sizeof(event), 0, 0);
}}

- (void)windowWillClose:(NSNotification *)notification {{
  if (pcc_lifecycle_sink == NULL) return;
  PccGuiNativeWindowEventV1 event = {{3, 0, 0, 0, 0}};
  pcc_lifecycle_sink(PCC_GUI_APP_WINDOW_EVENT, pcc_lifecycle_window_id,
                     &event, sizeof(event), 0, 0);
}}

- (void)application:(NSApplication *)application openFiles:(NSArray<NSString *> *)filenames {{
  if (pcc_lifecycle_sink != NULL) {{
    for (NSString *path in filenames) {{
      const char *bytes = [path UTF8String];
      if (bytes != NULL) {{
        pcc_lifecycle_sink(PCC_GUI_APP_OPENED, pcc_lifecycle_window_id,
                           bytes, (uint64_t)strlen(bytes), 0, 0);
      }}
    }}
  }}
  [application replyToOpenOrPrint:NSApplicationDelegateReplySuccess];
}}

- (BOOL)applicationShouldHandleReopen:(NSApplication *)application
                    hasVisibleWindows:(BOOL)hasVisibleWindows {{
  (void)application;
  (void)hasVisibleWindows;
  if (pcc_lifecycle_sink != NULL) {{
    pcc_lifecycle_sink(PCC_GUI_APP_REOPEN, pcc_lifecycle_window_id,
                       NULL, 0, 0, 0);
  }}
  return YES;
}}

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {{
  (void)sender;
  if (pcc_lifecycle_sink == NULL) return NSTerminateNow;
  int32_t status = pcc_lifecycle_sink(
      PCC_GUI_APP_EXIT_REQUESTED, pcc_lifecycle_window_id,
      NULL, 0, 0, 0);
  return status == 1 ? NSTerminateCancel : NSTerminateNow;
}}
@end

static PccGuiLifecycleDelegate *pcc_lifecycle_delegate = nil;

static void pcc_render_set_error(const char *msg, NSError *err) {{
  char buf[400] = {{0}};
  if (err != nil) {{
    const char *m = [[err localizedDescription] UTF8String];
    if (m != NULL) snprintf(buf, sizeof(buf), "%s", m);
  }}
  if (buf[0] == 0) snprintf(buf, sizeof(buf), "%s", msg);
  snprintf(pcc_render_last_error, sizeof(pcc_render_last_error), "%s", buf);
}}

static void pcc_render_clear_error(void) {{ pcc_render_last_error[0] = '\\0'; }}

int64_t pcc_gui_metal_render_copy_last_error(char *out, uint64_t out_len) {{
  if (out == NULL || out_len == 0) return 2;
  size_t n = strnlen(pcc_render_last_error, sizeof(pcc_render_last_error));
  if (n >= out_len) n = (size_t)out_len - 1;
  memcpy(out, pcc_render_last_error, n);
  out[n] = '\\0';
  return 0;
}}

/* Build two vertex arrays (float2 pos + float4 col) for count rects. */
static int pcc_build_vertices(const void *rects, const void *colors,
                              int64_t count, float **pos_out, float **col_out,
                              int64_t *vcount_out) {{
  NSUInteger vcount = (NSUInteger)count * 6;
  float *pos = (float *)calloc(vcount * 2, sizeof(float));
  float *col = (float *)calloc(vcount * 4, sizeof(float));
  if (pos == NULL || col == NULL) {{
    if (pos) free(pos);
    if (col) free(col);
    return -1;
  }}
  const int64_t *r = (const int64_t *)rects;
  const uint8_t *c = (const uint8_t *)colors;
  for (int64_t i = 0; i < count; i++) {{
    float x = (float)r[i*4+0], y = (float)r[i*4+1];
    float w = (float)r[i*4+2], h = (float)r[i*4+3];
    float cr = c[i*4+0]/255.0f, cg = c[i*4+1]/255.0f;
    float cb = c[i*4+2]/255.0f, ca = c[i*4+3]/255.0f;
    float px[12] = {{x,y, x+w,y, x+w,y+h, x,y, x+w,y+h, x,y+h}};
    float pc[24] = {{cr,cg,cb,ca, cr,cg,cb,ca, cr,cg,cb,ca,
                     cr,cg,cb,ca, cr,cg,cb,ca, cr,cg,cb,ca}};
    for (int v = 0; v < 6; v++) {{
      pos[(i*6+v)*2+0] = px[v*2+0];
      pos[(i*6+v)*2+1] = px[v*2+1];
      col[(i*6+v)*4+0] = pc[v*4+0];
      col[(i*6+v)*4+1] = pc[v*4+1];
      col[(i*6+v)*4+2] = pc[v*4+2];
      col[(i*6+v)*4+3] = pc[v*4+3];
    }}
  }}
  *pos_out = pos;
  *col_out = col;
  *vcount_out = vcount;
  return 0;
}}

/* Compile the MSL pipeline once per device (cached in static). */
static id<MTLRenderPipelineState> pcc_pipeline(id<MTLDevice> device,
                                                MTLPixelFormat fmt, int *err) {{
  static id<MTLRenderPipelineState> cached_rgba = nil;
  static id<MTLRenderPipelineState> cached_bgra = nil;
  static id<MTLDevice> cached_device = nil;
  if (fmt == MTLPixelFormatBGRA8Unorm) {{
    if (cached_bgra != nil && cached_device == device) {{
      *err = 0;
      return cached_bgra;
    }}
  }} else {{
    if (cached_rgba != nil && cached_device == device) {{
      *err = 0;
      return cached_rgba;
    }}
  }}
  NSError *error = nil;
  NSString *src = [NSString stringWithCString:"{msl}"
                                    encoding:NSUTF8StringEncoding];
  id<MTLLibrary> library = [device newLibraryWithSource:src options:nil error:&error];
  if (library == nil) {{
    pcc_render_set_error("newLibraryWithSource failed", error);
    *err = 5;
    return nil;
  }}
  id<MTLFunction> vs = [library newFunctionWithName:@"pcc_rect_vs"];
  id<MTLFunction> fs = [library newFunctionWithName:@"pcc_rect_fs"];
  if (vs == nil || fs == nil) {{
    pcc_render_set_error("newFunctionWithName failed", nil);
    *err = 6;
    return nil;
  }}
  MTLRenderPipelineDescriptor *pd = [[MTLRenderPipelineDescriptor alloc] init];
  pd.vertexFunction = vs;
  pd.fragmentFunction = fs;
  pd.colorAttachments[0].pixelFormat = fmt;
  id<MTLRenderPipelineState> pipeline =
      [device newRenderPipelineStateWithDescriptor:pd error:&error];
  if (pipeline == nil) {{
    pcc_render_set_error("newRenderPipelineStateWithDescriptor failed", error);
    *err = 7;
    return nil;
  }}
  if (fmt == MTLPixelFormatBGRA8Unorm) {{
    cached_bgra = pipeline;
  }} else {{
    cached_rgba = pipeline;
  }}
  cached_device = device;
  *err = 0;
  return pipeline;
}}

/* Render count rects into texture via one render pass. */
static int pcc_render_texture(id<MTLDevice> device, id<MTLTexture> tex,
                              const void *rects, const void *colors,
                              int64_t count, int64_t width, int64_t height,
                              MTLPixelFormat fmt) {{
  int perr = 0;
  id<MTLRenderPipelineState> pipeline = pcc_pipeline(device, fmt, &perr);
  if (perr != 0) return perr;
  float *pos = NULL, *col = NULL;
  int64_t vcount = 0;
  if (pcc_build_vertices(rects, colors, count, &pos, &col, &vcount) != 0) {{
    pcc_render_set_error("calloc failed", nil);
    return 9;
  }}
  float size2[2] = {{(float)width, (float)height}};
  MTLRenderPassDescriptor *rpd = [MTLRenderPassDescriptor renderPassDescriptor];
  rpd.colorAttachments[0].texture = tex;
  rpd.colorAttachments[0].loadAction = MTLLoadActionClear;
  rpd.colorAttachments[0].storeAction = MTLStoreActionStore;
  rpd.colorAttachments[0].clearColor = MTLClearColorMake(0.0, 0.0, 0.0, 1.0);
  id<MTLCommandQueue> queue = [device newCommandQueue];
  id<MTLCommandBuffer> cb = [queue commandBuffer];
  id<MTLRenderCommandEncoder> enc = [cb renderCommandEncoderWithDescriptor:rpd];
  [enc setRenderPipelineState:pipeline];
  [enc setVertexBytes:pos length:vcount*2*sizeof(float) atIndex:0];
  [enc setVertexBytes:col length:vcount*4*sizeof(float) atIndex:1];
  [enc setVertexBytes:size2 length:sizeof(size2) atIndex:2];
  [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:vcount];
  [enc endEncoding];
  [cb commit];
  [cb waitUntilCompleted];
  free(pos);
  free(col);
  return 0;
}}

/* rects: count * 32 bytes (x,y,w,h as int64).  colors: count * 4 bytes RGBA.
   out_pixels: width*height*4 bytes (RGBA8).  Returns 0 on success. */
int64_t pcc_gui_metal_render_offscreen(
    const void *rects, const void *colors, int64_t count,
    int64_t width, int64_t height,
    void *out_pixels, uint64_t out_len) {{
  @autoreleasepool {{
    pcc_render_clear_error();
    if (rects == NULL || colors == NULL || count <= 0) {{
      pcc_render_set_error("missing rects/colors", nil);
      return 2;
    }}
    if (width <= 0 || height <= 0 || out_pixels == NULL ||
        out_len < (uint64_t)width * (uint64_t)height * 4) {{
      pcc_render_set_error("bad size or output buffer", nil);
      return 3;
    }}
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {{
      pcc_render_set_error("MTLCreateSystemDefaultDevice returned nil", nil);
      return 4;
    }}
    MTLTextureDescriptor *td = [MTLTextureDescriptor
        texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
        width:(NSUInteger)width height:(NSUInteger)height mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    id<MTLTexture> tex = [device newTextureWithDescriptor:td];
    if (tex == nil) {{
      pcc_render_set_error("newTextureWithDescriptor failed", nil);
      return 8;
    }}
    int rc = pcc_render_texture(device, tex, rects, colors, count, width, height,
                                MTLPixelFormatRGBA8Unorm);
    if (rc != 0) return rc;
    [tex getBytes:out_pixels bytesPerRow:(NSUInteger)width*4
        fromRegion:MTLRegionMake2D(0, 0, (NSUInteger)width, (NSUInteger)height)
        mipmapLevel:0];
    return 0;
  }}
}}

/* ---- Windowed path: NSWindow + CAMetalLayer + present ---- */

static void pcc_install_app_menu(NSString *appName) {{
  NSMenu *mainMenu = [[NSMenu alloc] init];
  NSMenuItem *appItem = [[NSMenuItem alloc] init];
  [mainMenu addItem:appItem];
  NSMenu *appMenu = [[NSMenu alloc] init];
  NSString *aboutTitle = [NSString stringWithFormat:@"About %@", appName];
  [appMenu addItemWithTitle:aboutTitle
                     action:@selector(orderFrontStandardAboutPanel:)
              keyEquivalent:@""];
  [appMenu addItem:[NSMenuItem separatorItem]];
  [appMenu addItemWithTitle:[NSString stringWithFormat:@"Quit %@", appName]
                     action:@selector(terminate:) keyEquivalent:@"q"];
  [appItem setSubmenu:appMenu];
  NSMenuItem *windowItem = [[NSMenuItem alloc] init];
  [mainMenu addItem:windowItem];
  NSMenu *windowMenu = [[NSMenu alloc] init];
  [windowMenu addItemWithTitle:@"Minimize" action:@selector(performMiniaturize:) keyEquivalent:@"m"];
  [windowMenu addItemWithTitle:@"Zoom" action:@selector(performZoom:) keyEquivalent:@""];
  [windowItem setSubmenu:windowMenu];
  NSMenuItem *editItem = [[NSMenuItem alloc] init];
  [mainMenu addItem:editItem];
  NSMenu *editMenu = [[NSMenu alloc] initWithTitle:@"Edit"];
  [editMenu addItemWithTitle:@"Cut" action:@selector(cut:) keyEquivalent:@"x"];
  [editMenu addItemWithTitle:@"Copy" action:@selector(copy:) keyEquivalent:@"c"];
  [editMenu addItemWithTitle:@"Paste" action:@selector(paste:) keyEquivalent:@"v"];
  [editMenu addItem:[NSMenuItem separatorItem]];
  [editMenu addItemWithTitle:@"Select All" action:@selector(selectAll:) keyEquivalent:@"a"];
  [editItem setSubmenu:editMenu];
  [NSApp setMainMenu:mainMenu];
  [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
}}

/* ---- CoreGraphics text+rect frame renderer (unified layer) ---- */

static id<MTLRenderPipelineState> pcc_tex_pipeline(id<MTLDevice> device,
                                                   MTLPixelFormat fmt, int *err) {{
  static id<MTLRenderPipelineState> t_rgba = nil, t_bgra = nil;
  static id<MTLDevice> t_dev = nil;
  NSError *e = nil;
  NSString *src = [NSString stringWithCString:"{msl}"
                                    encoding:NSUTF8StringEncoding];
  id<MTLLibrary> lib = [device newLibraryWithSource:src options:nil error:&e];
  if (lib == nil) {{ pcc_render_set_error("newLibraryWithSource(tex) failed", e); *err = 5; return nil; }}
  id<MTLFunction> vs = [lib newFunctionWithName:@"pcc_tex_vs"];
  id<MTLFunction> fs = [lib newFunctionWithName:@"pcc_tex_fs"];
  if (vs == nil || fs == nil) {{ pcc_render_set_error("tex shaders missing", nil); *err = 6; return nil; }}
  MTLRenderPipelineDescriptor *pd = [[MTLRenderPipelineDescriptor alloc] init];
  pd.vertexFunction = vs;
  pd.fragmentFunction = fs;
  pd.colorAttachments[0].pixelFormat = fmt;
  id<MTLRenderPipelineState> pl = [device newRenderPipelineStateWithDescriptor:pd error:&e];
  if (pl == nil) {{ pcc_render_set_error("tex pipeline failed", e); *err = 7; return nil; }}
  if (fmt == MTLPixelFormatBGRA8Unorm) t_bgra = pl; else t_rgba = pl;
  t_dev = device;
  *err = 0;
  return pl;
}}

/* texts: N * 48 bytes {{x@0, y@8, len@16, font@24, color@32, ptr@40}} */
int64_t pcc_gui_metal_window_present_cg(
    void *handle,
    const void *rects, const void *colors, const void *texts,
    int64_t rcount, int64_t tcount) {{
  @autoreleasepool {{
    pcc_render_clear_error();
    if (handle == NULL) return 2;
    NSWindow *win0 = (__bridge NSWindow *)handle;
    NSRect bounds0 = [[win0 contentView] bounds];
    int64_t width = (int64_t)bounds0.size.width;
    int64_t height = (int64_t)bounds0.size.height;
    if (width <= 0 || height <= 0) return 2;
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) return 3;
    /* draw the whole frame with CoreGraphics: rects + text into one bitmap */
    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    uint8_t *buf = (uint8_t *)calloc((size_t)width * (size_t)height * 4, 1);
    if (buf == NULL) return 4;
    CGContextRef ctx = CGBitmapContextCreate(
        buf, (size_t)width, (size_t)height, 8, (size_t)width * 4, cs,
        kCGImageAlphaPremultipliedLast | kCGBitmapByteOrder32Big);
    CGColorSpaceRelease(cs);
    if (ctx == NULL) {{ free(buf); return 5; }}
    /* Quartz origin is bottom-left; flip so we draw in top-left coords */
    CGContextTranslateCTM(ctx, 0, (CGFloat)height);
    CGContextScaleCTM(ctx, 1.0, -1.0);
    CGContextSetAllowsAntialiasing(ctx, false);
    /* rectangles */
    const int64_t *r = (const int64_t *)rects;
    const uint8_t *c = (const uint8_t *)colors;
    for (int64_t i = 0; i < rcount; i++) {{
      CGFloat rr = c[i*4+0]/255.0, gg = c[i*4+1]/255.0;
      CGFloat bb = c[i*4+2]/255.0, aa = c[i*4+3]/255.0;
      CGContextSetRGBFillColor(ctx, rr, gg, bb, aa);
      CGContextFillRect(ctx, CGRectMake((CGFloat)r[i*4+0], (CGFloat)r[i*4+1],
                                        (CGFloat)r[i*4+2], (CGFloat)r[i*4+3]));
    }}
    /* text via CoreText */
    CGContextSetAllowsAntialiasing(ctx, true);
    const uint8_t *tx = (const uint8_t *)texts;
    for (int64_t i = 0; i < tcount; i++) {{
      const int64_t *tf = (const int64_t *)(tx + i * 48);
      int64_t tx0 = tf[0], ty0 = tf[1], tlen = tf[2], tfont = tf[3];
      int64_t tcolor = tf[4];
      const char *tptr = (const char *)(intptr_t)tf[5];
      if (tptr == NULL || tlen <= 0) continue;
      CTFontRef font = CTFontCreateWithName(CFSTR("Helvetica"), (CGFloat)tfont, NULL);
      if (font == NULL) continue;
      CFStringRef str = CFStringCreateWithBytes(
          NULL, (const uint8_t *)tptr, (CFIndex)tlen,
          kCFStringEncodingUTF8, NO);
      if (str == NULL) {{ CFRelease(font); continue; }}
      CFMutableDictionaryRef attrs = CFDictionaryCreateMutable(
          NULL, 2, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
      CFDictionaryAddValue(attrs, kCTFontAttributeName, font);
      CGFloat rr = (CGFloat)((tcolor >> 16) & 255) / 255.0;
      CGFloat gg = (CGFloat)((tcolor >> 8) & 255) / 255.0;
      CGFloat bb = (CGFloat)(tcolor & 255) / 255.0;
      CGFloat aa = (CGFloat)((tcolor >> 24) & 255) / 255.0;
      CGColorRef cc = CGColorCreateGenericRGB(rr, gg, bb, aa);
      CFDictionaryAddValue(attrs, kCTForegroundColorAttributeName, cc);
      CGColorRelease(cc);
      CFAttributedStringRef as = CFAttributedStringCreate(NULL, str, attrs);
      CTLineRef line = CTLineCreateWithAttributedString(as);
      CGContextSetTextPosition(ctx, (CGFloat)tx0, (CGFloat)ty0 + 4.0);
      CTLineDraw(line, ctx);
      CFRelease(line); CFRelease(as); CFRelease(attrs);
      CFRelease(str); CFRelease(font);
    }}
    CGContextFlush(ctx);
    /* upload bitmap to a texture */
    MTLTextureDescriptor *td = [MTLTextureDescriptor
        texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
        width:(NSUInteger)width height:(NSUInteger)height mipmapped:NO];
    td.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> tex = [device newTextureWithDescriptor:td];
    [tex replaceRegion:MTLRegionMake2D(0,0,(NSUInteger)width,(NSUInteger)height)
           mipmapLevel:0 withBytes:buf bytesPerRow:(NSUInteger)width*4];
    CGContextRelease(ctx);
    free(buf);
    NSWindow *win = (__bridge NSWindow *)handle;
    CAMetalLayer *layer = pcc_metal_layer;
    [pcc_metal_view setFrame:[[win contentView] bounds]];
    layer.frame = [[win contentView] bounds];
    layer.drawableSize = CGSizeMake((CGFloat)width, (CGFloat)height);
    id<CAMetalDrawable> drawable = [layer nextDrawable];
    if (drawable == nil) return 6;
    int perr = 0;
    id<MTLRenderPipelineState> pl = pcc_tex_pipeline(device, drawable.texture.pixelFormat, &perr);
    if (perr != 0) return perr;
    MTLRenderPassDescriptor *rpd = [MTLRenderPassDescriptor renderPassDescriptor];
    rpd.colorAttachments[0].texture = drawable.texture;
    rpd.colorAttachments[0].loadAction = MTLLoadActionClear;
    rpd.colorAttachments[0].storeAction = MTLStoreActionStore;
    rpd.colorAttachments[0].clearColor = MTLClearColorMake(0,0,0,1);
    id<MTLCommandQueue> q = [device newCommandQueue];
    id<MTLCommandBuffer> cb = [q commandBuffer];
    id<MTLRenderCommandEncoder> enc = [cb renderCommandEncoderWithDescriptor:rpd];
    [enc setRenderPipelineState:pl];
    [enc setFragmentTexture:tex atIndex:0];
    [enc drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
    [enc endEncoding];
    [cb presentDrawable:drawable];
    [cb commit];
    [cb waitUntilCompleted];
    return 0;
  }}
}}

int64_t pcc_gui_metal_lifecycle_install(
    void *handle, uint64_t window_id, PccGuiLifecycleSink sink) {{
  @autoreleasepool {{
    if (handle == NULL || sink == NULL) return 2;
    NSWindow *window = (__bridge NSWindow *)handle;
    if (pcc_lifecycle_delegate == nil) {{
      pcc_lifecycle_delegate = [[PccGuiLifecycleDelegate alloc] init];
    }}
    pcc_lifecycle_sink = sink;
    pcc_lifecycle_window_id = window_id;
    [NSApp setDelegate:pcc_lifecycle_delegate];
    [window setDelegate:pcc_lifecycle_delegate];
    return 0;
  }}
}}

int64_t pcc_gui_metal_lifecycle_probe(void *handle, const char *opened_path) {{
  @autoreleasepool {{
    if (handle == NULL || opened_path == NULL || pcc_lifecycle_delegate == nil ||
        pcc_lifecycle_sink == NULL) return 2;
    NSWindow *window = (__bridge NSWindow *)handle;
    NSRect frame = [window frame];
    frame.size.width = frame.size.width + 1.0;
    [window setFrame:frame display:YES];
    [pcc_lifecycle_delegate windowDidResize:
        [NSNotification notificationWithName:NSWindowDidResizeNotification
                                      object:window]];
    NSString *path = [NSString stringWithUTF8String:opened_path];
    if (path == nil) return 3;
    [pcc_lifecycle_delegate application:NSApp openFiles:@[path]];
    [pcc_lifecycle_delegate applicationShouldHandleReopen:NSApp
                                        hasVisibleWindows:[window isVisible]];
    return 0;
  }}
}}

void *pcc_gui_metal_window_create(const char *title, int64_t width, int64_t height) {{
  @autoreleasepool {{
    NSApplication *app = [NSApplication sharedApplication];
    [app setActivationPolicy:NSApplicationActivationPolicyRegular];
    NSString *appName = [NSString stringWithUTF8String:(title != NULL ? title : "pcc")];
    pcc_install_app_menu(appName);
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    NSRect frame = NSMakeRect(0, 0, (CGFloat)width, (CGFloat)height);
    NSWindow *win = [[NSWindow alloc]
        initWithContentRect:frame
        styleMask:(NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                 NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
        backing:NSBackingStoreBuffered defer:NO];
    if (win == nil) return NULL;
    if (title != NULL) [win setTitle:[NSString stringWithUTF8String:title]];
    [win center];
    CAMetalLayer *layer = [CAMetalLayer layer];
    layer.device = MTLCreateSystemDefaultDevice();
    layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    layer.framebufferOnly = YES;
    layer.drawableSize = CGSizeMake((CGFloat)width, (CGFloat)height);
    /* Layer-BACKED content view (AppKit-managed) with the Metal surface in a
       dedicated bottom subview, so native controls (selectable NSTextFields)
       can live as sibling subviews on top.  A layer-HOSTING content view
       (setLayer:) forbids subviews. */
    NSView *cv = [win contentView];
    [cv setWantsLayer:YES];
    cv.layer.backgroundColor = [[NSColor whiteColor] CGColor];
    NSView *mv = [[NSView alloc] initWithFrame:[cv bounds]];
    /* layer-HOSTING view: set the custom layer FIRST, then wantsLayer, or the
       CAMetalLayer is not presented and the view shows an opaque black. */
    [mv setLayer:layer];
    [mv setWantsLayer:YES];
    [mv setAutoresizingMask:(NSViewWidthSizable | NSViewHeightSizable)];
    [cv addSubview:mv];
    pcc_metal_layer = layer;
    pcc_metal_view = mv;
    return (__bridge_retained void *)win;
  }}
}}

int64_t pcc_gui_metal_window_render(void *handle, const void *rects,
                                    const void *colors, int64_t count,
                                    int64_t width, int64_t height) {{
  @autoreleasepool {{
    pcc_render_clear_error();
    if (handle == NULL || rects == NULL || colors == NULL || count <= 0) {{
      pcc_render_set_error("missing args", nil);
      return 2;
    }}
    NSWindow *win = (__bridge NSWindow *)handle;
    CAMetalLayer *layer = pcc_metal_layer;
    if (layer == nil) {{
      pcc_render_set_error("window has no CAMetalLayer", nil);
      return 3;
    }}
    /* Follow the window: metal subview + layer track the content bounds. */
    [pcc_metal_view setFrame:[[win contentView] bounds]];
    layer.frame = [[win contentView] bounds];
    layer.drawableSize = CGSizeMake((CGFloat)width, (CGFloat)height);
    id<CAMetalDrawable> drawable = [layer nextDrawable];
    if (drawable == nil) {{
      pcc_render_set_error("nextDrawable returned nil", nil);
      return 4;
    }}
    int rc = pcc_render_texture(layer.device, drawable.texture, rects, colors,
                                count, width, height,
                                drawable.texture.pixelFormat);
    if (rc != 0) return rc;
    id<MTLCommandQueue> queue = [layer.device newCommandQueue];
    id<MTLCommandBuffer> cb = [queue commandBuffer];
    [cb presentDrawable:drawable];
    [cb commit];
    [cb waitUntilCompleted];
    return 0;
  }}
}}

int64_t pcc_gui_metal_window_show(void *handle) {{
  @autoreleasepool {{
    if (handle == NULL) return -1;
    NSWindow *win = (__bridge NSWindow *)handle;
    [win makeKeyAndOrderFront:nil];
    [win makeKeyWindow];
    [NSApp activateIgnoringOtherApps:YES];
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    return 0;
  }}
}}

int64_t pcc_gui_metal_run_loop_pump(void) {{
  @autoreleasepool {{
    /* Let AppKit service window display/events without a dedicated run loop:
       pump the default mode briefly.  Safe to call repeatedly from the
       client's own main loop. */
    /* Manual AppKit event loop: dispatch queued events (window controls,
       clicks, key presses) and wait up to 20ms for the next one.  This
       services close/min/zoom buttons without a dedicated [NSApp run]. */
    NSEvent *event = nil;
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:0.020];
    while ((event = [NSApp nextEventMatchingMask:NSEventMaskAny
                                       untilDate:deadline
                                          inMode:NSDefaultRunLoopMode
                                         dequeue:YES]) != nil) {{
      if ([event type] == NSEventTypeLeftMouseDown) {{
        NSPoint pt = [event locationInWindow];
        NSWindow *w = [event window];
        CGFloat ch = (w != nil) ? [[w contentView] bounds].size.height : 0.0;
        /* convert to top-left origin like the renderer */
        pcc_last_click = ((uint64_t)(uint32_t)(int32_t)(ch - pt.y) << 32)
                       | (uint32_t)(int32_t)pt.x;
      }}
      if ([event type] != NSEventTypeMouseMoved &&
          [event type] != NSEventTypeMouseEntered &&
          [event type] != NSEventTypeMouseExited &&
          [event type] != NSEventTypeCursorUpdate) {{
        [NSApp sendEvent:event];
        [NSApp updateWindows];
      }}
      deadline = [NSDate dateWithTimeIntervalSinceNow:0.001];
    }}
    return 0;
  }}
}}

static NSScrollView *pcc_panes[2];
static NSTextView *pcc_pane_views[2];
static NSString *pcc_pane_last[2];

/* Non-selectable line-number gutter (NSRulerView auto-syncs with the scroll
   view, and is NOT part of the text, so selection/copy never include it). */
@interface PccLineRuler : NSRulerView {{
  const int64_t *_spec;
  int64_t _nlines;
}}
@end
@implementation PccLineRuler
- (void)setSpec:(const int64_t *)spec nlines:(int64_t)n {{
  _spec = spec; _nlines = n; [self setNeedsDisplay:YES];
}}
- (void)drawHashMarksAndLabelsInRect:(NSRect)rect {{
  (void)rect;
  NSScrollView *sv = [self scrollView];
  NSTextView *tv = (NSTextView *)[sv documentView];
  if (tv == nil || _spec == NULL) return;
  NSLayoutManager *lm = [tv layoutManager];
  NSRect vis = [[sv contentView] bounds];
  CGFloat insetH = [tv textContainerInset].height;
  NSDictionary *attrs = @{{
    NSFontAttributeName: [NSFont monospacedSystemFontOfSize:11.0 weight:NSFontWeightRegular],
    NSForegroundColorAttributeName: [NSColor grayColor]
  }};
  NSUInteger numGlyphs = [lm numberOfGlyphs];
  NSUInteger gi = 0;
  int64_t idx = 0;
  CGFloat thick = [self ruleThickness];
  while (idx < _nlines && gi < numGlyphs) {{
    NSRange lr;
    NSRect fr = [lm lineFragmentRectForGlyphAtIndex:gi effectiveRange:&lr];
    int64_t lno = _spec[idx*6+5];
    if (lno > 0) {{
      CGFloat y = fr.origin.y + insetH - vis.origin.y;
      NSString *str = [NSString stringWithFormat:@"%lld", (long long)lno];
      NSSize sz = [str sizeWithAttributes:attrs];
      [str drawAtPoint:NSMakePoint(thick - sz.width - 5.0, y) withAttributes:attrs];
    }}
    gi = NSMaxRange(lr);
    idx = idx + 1;
  }}
}}
@end
static PccLineRuler *pcc_pane_rulers[2];

/* Byte offset in UTF-8 `utf8` -> UTF-16 code-unit offset (for NSRange). */
static NSUInteger pcc_u16_from_byte(const char *utf8, int64_t byteoff) {{
  if (byteoff <= 0) return 0;
  NSString *pre = [[NSString alloc] initWithBytes:utf8 length:(NSUInteger)byteoff
                                         encoding:NSUTF8StringEncoding];
  return (pre != nil) ? [pre length] : 0;
}}

/* Set a whole diff pane as ONE selectable NSTextView (region selection across
   lines). `utf8` is the aligned pane text (line-number + content, blank lines
   for orphans). `spec` is nlines records of 5 x int64:
   {{line_byte_start, line_byte_len, kind, red_byte_start, red_byte_len}};
   kind 0=equal(no band) 1=diff(pink) 2=gap(grey). Rebuilds only when the text
   changes, so an active selection survives the client's redraw loop. */
int64_t pcc_gui_metal_pane_set(void *handle, const char *utf8, void *params,
    int64_t len, int64_t nlines, int64_t pane) {{
  @autoreleasepool {{
    if (handle == NULL || params == NULL || pane < 0 || pane > 1 || utf8 == NULL) return 2;
    const int64_t *pp = (const int64_t *)params;
    int64_t x = pp[0], y = pp[1], w = pp[2], h = pp[3];
    const void *spec = (const void *)(intptr_t)pp[4];
    NSWindow *win = (__bridge NSWindow *)handle;
    NSView *cv = [win contentView];
    NSFont *mono = [NSFont monospacedSystemFontOfSize:13.0 weight:NSFontWeightRegular];
    if (pcc_panes[pane] == nil) {{
      NSScrollView *sv = [[NSScrollView alloc] initWithFrame:CGRectMake((CGFloat)x, 0, (CGFloat)w, (CGFloat)h)];
      [sv setHasVerticalScroller:YES];
      [sv setHasHorizontalScroller:NO];
      [sv setDrawsBackground:NO];
      [sv setBorderType:NSNoBorder];
      NSTextView *tv = [[NSTextView alloc] initWithFrame:CGRectMake(0, 0, (CGFloat)w, (CGFloat)h)];
      [tv setEditable:NO];
      [tv setSelectable:YES];
      [tv setRichText:YES];
      [tv setDrawsBackground:NO];
      [tv setFont:mono];
      [tv setTextContainerInset:NSMakeSize(2.0, 2.0)];
      [tv setVerticallyResizable:YES];
      [tv setHorizontallyResizable:YES];
      [[tv textContainer] setWidthTracksTextView:NO];
      [[tv textContainer] setContainerSize:NSMakeSize(1.0e7, 1.0e7)];
      [sv setHasHorizontalScroller:YES];
      [sv setDocumentView:tv];
      PccLineRuler *ruler = [[PccLineRuler alloc] initWithScrollView:sv orientation:NSVerticalRuler];
      [ruler setRuleThickness:46.0];
      [sv setVerticalRulerView:ruler];
      [sv setHasVerticalRuler:YES];
      [sv setRulersVisible:YES];
      [cv addSubview:sv];
      pcc_panes[pane] = sv;
      pcc_pane_views[pane] = tv;
      pcc_pane_rulers[pane] = ruler;
    }}
    CGFloat vh = [cv bounds].size.height;
    [pcc_panes[pane] setFrame:CGRectMake((CGFloat)x, vh - (CGFloat)y - (CGFloat)h,
                                         (CGFloat)w, (CGFloat)h)];
    NSString *snew = [[NSString alloc] initWithBytes:utf8 length:(NSUInteger)len
                                            encoding:NSUTF8StringEncoding];
    if (snew == nil) return 3;
    if (pcc_pane_last[pane] != nil && [pcc_pane_last[pane] isEqualToString:snew]) return 0;
    pcc_pane_last[pane] = snew;
    NSTextView *tv = pcc_pane_views[pane];
    NSUInteger tot = [snew length];
    NSMutableAttributedString *as = [[NSMutableAttributedString alloc] initWithString:snew];
    [as addAttribute:NSFontAttributeName value:mono range:NSMakeRange(0, tot)];
    [as addAttribute:NSForegroundColorAttributeName
               value:[NSColor colorWithSRGBRed:0.10 green:0.10 blue:0.10 alpha:1.0]
               range:NSMakeRange(0, tot)];
    const int64_t *sp = (const int64_t *)spec;
    for (int64_t i = 0; i < nlines; i++) {{
      int64_t lb = sp[i*6+0], ll = sp[i*6+1], kind = sp[i*6+2];
      int64_t rb = sp[i*6+3], rl = sp[i*6+4];
      if (ll > 0 && kind != 0) {{
        NSUInteger u0 = pcc_u16_from_byte(utf8, lb);
        NSUInteger u1 = pcc_u16_from_byte(utf8, lb + ll);
        if (u1 > tot) u1 = tot;
        if (u0 <= u1) {{
          NSColor *bg = (kind == 2)
            ? [NSColor colorWithSRGBRed:0.94 green:0.94 blue:0.94 alpha:1.0]
            : [NSColor colorWithSRGBRed:0.99 green:0.89 blue:0.89 alpha:1.0];
          [as addAttribute:NSBackgroundColorAttributeName value:bg
                     range:NSMakeRange(u0, u1 - u0)];
        }}
      }}
      if (rl > 0) {{
        NSUInteger r0 = pcc_u16_from_byte(utf8, rb);
        NSUInteger r1 = pcc_u16_from_byte(utf8, rb + rl);
        if (r1 > tot) r1 = tot;
        if (r0 <= r1) {{
          [as addAttribute:NSForegroundColorAttributeName
                     value:[NSColor colorWithSRGBRed:0.80 green:0.10 blue:0.12 alpha:1.0]
                     range:NSMakeRange(r0, r1 - r0)];
          [as addAttribute:NSBackgroundColorAttributeName
                     value:[NSColor colorWithSRGBRed:1.0 green:0.78 blue:0.78 alpha:1.0]
                     range:NSMakeRange(r0, r1 - r0)];
        }}
      }}
    }}
    [[tv textStorage] setAttributedString:as];
    if (pcc_pane_rulers[pane] != nil)
      [pcc_pane_rulers[pane] setSpec:(const int64_t *)spec nlines:nlines];
    return 0;
  }}
}}

/* Select + scroll a diff pane to line `line_index` (0-based display line).
   The NSTextView selection IS the current-diff highlight, and it scrolls into
   view. Returns 0 on success. */
int64_t pcc_gui_metal_pane_focus(void *handle, int64_t pane, int64_t line_index) {{
  @autoreleasepool {{
    if (handle == NULL || pane < 0 || pane > 1) return 2;
    NSTextView *tv = pcc_pane_views[pane];
    if (tv == nil) return 3;
    NSString *sv = [tv string];
    NSUInteger len = [sv length];
    NSUInteger pos = 0;
    int64_t line = 0;
    NSUInteger lineStart = 0;
    while (line < line_index && pos < len) {{
      if ([sv characterAtIndex:pos] == 10) {{ line = line + 1; lineStart = pos + 1; }}
      pos = pos + 1;
    }}
    NSUInteger lineEnd = lineStart;
    while (lineEnd < len && [sv characterAtIndex:lineEnd] != 10) lineEnd = lineEnd + 1;
    NSRange r = NSMakeRange(lineStart, lineEnd - lineStart);
    [tv setSelectedRange:r];
    [tv scrollRangeToVisible:r];
    if (pcc_pane_rulers[pane] != nil) [pcc_pane_rulers[pane] setNeedsDisplay:YES];
    return 0;
  }}
}}

int64_t pcc_gui_metal_window_text(void *handle, const char *text, void *params,
                                      int64_t len, int64_t font, int64_t color) {{
  @autoreleasepool {{
    if (handle == NULL || params == NULL) return 2;
    const int64_t *prm = (const int64_t *)params;
    int64_t slot = prm[0], x = prm[1], y = prm[2];
    if (slot < 0 || slot >= 512) return 2;
    NSWindow *win = (__bridge NSWindow *)handle;
    NSView *cv = [win contentView];
    CGFloat vh = [cv bounds].size.height;
    CGFloat vw = [cv bounds].size.width;
    CGFloat r = (CGFloat)((color >> 16) & 255) / 255.0;
    CGFloat g = (CGFloat)((color >> 8) & 255) / 255.0;
    CGFloat b = (CGFloat)(color & 255) / 255.0;
    CGFloat a = (CGFloat)((color >> 24) & 255) / 255.0;

    /* slots < 20 are chrome (toolbar/status labels): non-interactive
       CATextLayer on the Metal layer, so clicks fall through to the
       Metal-drawn buttons underneath. */
    if (slot < 20) {{
      if (text == NULL || len <= 0) {{
        if (pcc_text_layers[slot] != nil) pcc_text_layers[slot].hidden = YES;
        return 0;
      }}
      if (pcc_text_layers[slot] == nil) {{
        pcc_text_layers[slot] = [CATextLayer layer];
        pcc_text_layers[slot].wrapped = NO;
        pcc_text_layers[slot].contentsScale = [[NSScreen mainScreen] backingScaleFactor];
        [pcc_metal_layer addSublayer:pcc_text_layers[slot]];
      }}
      CATextLayer *tl = pcc_text_layers[slot];
      NSString *s = [[NSString alloc] initWithBytes:text length:(NSUInteger)len
                                           encoding:NSUTF8StringEncoding];
      if (s == nil) return 3;
      tl.string = s;
      tl.fontSize = (CGFloat)font;
      CGColorRef cg = CGColorCreateGenericRGB(r, g, b, a);
      tl.foregroundColor = cg;
      CGColorRelease(cg);
      CGFloat ty = vh - (CGFloat)y - (CGFloat)font - 4.0;
      tl.frame = CGRectMake((CGFloat)x, ty, 400.0, (CGFloat)font + 6.0);
      tl.hidden = NO;
      return 0;
    }}

    /* slots >= 20 are document text: real selectable NSTextField subviews so
       the user can select/copy.  Mutate ONLY on change -- the client redraws
       every 16ms and re-setting stringValue would wipe the selection. */
    if (text == NULL || len <= 0) {{
      if (pcc_text_fields[slot] != nil) [pcc_text_fields[slot] setHidden:YES];
      return 0;
    }}
    if (pcc_text_fields[slot] == nil) {{
      NSTextField *nf = [[NSTextField alloc] initWithFrame:CGRectZero];
      [nf setBezeled:NO];
      [nf setBordered:NO];
      [nf setDrawsBackground:NO];
      [nf setEditable:NO];
      [nf setSelectable:YES];
      [[nf cell] setUsesSingleLineMode:YES];
      [[nf cell] setLineBreakMode:NSLineBreakByClipping];
      [cv addSubview:nf];
      pcc_text_fields[slot] = nf;
    }}
    NSTextField *tf = pcc_text_fields[slot];
    NSString *s = [[NSString alloc] initWithBytes:text length:(NSUInteger)len
                                         encoding:NSUTF8StringEncoding];
    if (s == nil) return 3;
    NSFont *want = [NSFont monospacedSystemFontOfSize:(CGFloat)font
                                               weight:NSFontWeightRegular];
    if (want != nil && ![[tf font] isEqual:want]) [tf setFont:want];
    NSColor *col = [NSColor colorWithSRGBRed:r green:g blue:b alpha:a];
    if (![[tf textColor] isEqual:col]) [tf setTextColor:col];
    /* Only touch the field when the text actually changes, else the 16ms
       redraw loop would wipe the user's selection every frame. */
    if (![[tf stringValue] isEqualToString:s]) {{
      int64_t hstart = prm[3];
      int64_t hlen = prm[4];
      if (hlen > 0 && hstart >= 0) {{
        /* Inline diff highlight: the differing byte range [hstart, hstart+hlen)
           (computed in pcc-Python) is painted red. Convert UTF-8 byte offsets
           to UTF-16 code-unit offsets for the NSRange. */
        NSMutableAttributedString *as =
            [[NSMutableAttributedString alloc] initWithString:s];
        NSUInteger slen2 = [s length];
        [as addAttribute:NSForegroundColorAttributeName value:col
                   range:NSMakeRange(0, slen2)];
        if (want != nil)
          [as addAttribute:NSFontAttributeName value:want
                     range:NSMakeRange(0, slen2)];
        NSString *pre = [[NSString alloc] initWithBytes:text
            length:(NSUInteger)hstart encoding:NSUTF8StringEncoding];
        NSString *pmid = [[NSString alloc] initWithBytes:text
            length:(NSUInteger)(hstart + hlen) encoding:NSUTF8StringEncoding];
        NSUInteger u0 = (pre != nil) ? [pre length] : 0;
        NSUInteger u1 = (pmid != nil) ? [pmid length] : u0;
        if (u1 > slen2) u1 = slen2;
        if (u0 <= u1) {{
          NSRange hr = NSMakeRange(u0, u1 - u0);
          [as addAttribute:NSForegroundColorAttributeName
                     value:[NSColor colorWithSRGBRed:0.80 green:0.10 blue:0.12 alpha:1.0]
                     range:hr];
          [as addAttribute:NSBackgroundColorAttributeName
                     value:[NSColor colorWithSRGBRed:1.0 green:0.80 blue:0.80 alpha:1.0]
                     range:hr];
        }}
        [tf setAttributedStringValue:as];
      }} else {{
        [tf setStringValue:s];
      }}
    }}
    /* clip each field to its own column so it never intercepts clicks in the
       other pane or over the difference-overview bar at the right edge */
    CGFloat half = vw / 2.0;
    CGFloat fw = (x < half) ? (half - (CGFloat)x - 4.0) : (vw - (CGFloat)x - 18.0);
    if (fw < 8.0) fw = 8.0;
    CGFloat ty = vh - (CGFloat)y - (CGFloat)font - 6.0;
    CGRect fr = CGRectMake((CGFloat)x, ty, fw, (CGFloat)font + 8.0);
    if (!CGRectEqualToRect([tf frame], fr)) [tf setFrame:fr];
    if ([tf isHidden]) [tf setHidden:NO];
    return 0;
  }}
}}

int64_t pcc_gui_metal_window_capture(void *handle, const char *path) {{
  @autoreleasepool {{
    if (handle == NULL || path == NULL) return 2;
    NSWindow *win = (__bridge NSWindow *)handle;
    NSView *cv = [win contentView];
    NSBitmapImageRep *rep = [cv bitmapImageRepForCachingDisplayInRect:[cv bounds]];
    if (rep == nil) return 3;
    [cv cacheDisplayInRect:[cv bounds] toBitmapImageRep:rep];
    NSData *png = [rep representationUsingType:NSBitmapImageFileTypePNG properties:@{{}}];
    if (png == nil) return 4;
    NSString *sp = [NSString stringWithUTF8String:path];
    [png writeToFile:sp atomically:YES];
    return 0;
  }}
}}

int64_t pcc_gui_metal_open_panel(char *path_buf, int64_t cap) {{
  @autoreleasepool {{
    if (path_buf == NULL || cap <= 0) return 2;
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseFiles = YES;
    panel.canChooseDirectories = NO;
    panel.allowsMultipleSelection = NO;
    if ([panel runModal] != NSModalResponseOK) return 1;  /* cancelled */
    NSURL *url = [[panel URLs] firstObject];
    if (url == nil) return 1;
    const char *p = [[url path] UTF8String];
    if (p == NULL) return 1;
    size_t n = strlen(p);
    if (n >= (size_t)cap) n = (size_t)cap - 1;
    memcpy(path_buf, p, n);
    path_buf[n] = 0;
    return 0;
  }}
}}

int64_t pcc_gui_metal_open_panel2(char *p1, char *p2) {{
  @autoreleasepool {{
    if (p1 == NULL || p2 == NULL) return 2;
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseFiles = YES;
    panel.canChooseDirectories = NO;
    panel.allowsMultipleSelection = YES;
    panel.message = @"Select two files to compare";
    if ([panel runModal] != NSModalResponseOK) return 1;
    NSArray<NSURL *> *urls = [panel URLs];
    if ([urls count] < 2) return 1;
    const char *a = [[[urls objectAtIndex:0] path] UTF8String];
    const char *b = [[[urls objectAtIndex:1] path] UTF8String];
    if (a == NULL || b == NULL) return 1;
    size_t na = strlen(a); if (na >= 511) na = 511;
    size_t nb = strlen(b); if (nb >= 511) nb = 511;
    memcpy(p1, a, na); p1[na] = 0;
    memcpy(p2, b, nb); p2[nb] = 0;
    return 0;
  }}
}}

int64_t pcc_gui_metal_window_size(void *handle, int64_t *w_out, int64_t *h_out) {{
  @autoreleasepool {{
    if (handle == NULL || w_out == NULL || h_out == NULL) return 2;
    NSWindow *win = (__bridge NSWindow *)handle;
    NSRect b = [[win contentView] bounds];
    *w_out = (int64_t)b.size.width;
    *h_out = (int64_t)b.size.height;
    return 0;
  }}
}}

int64_t pcc_gui_metal_window_poll_click(void *handle, int64_t *x_out, int64_t *y_out) {{
  @autoreleasepool {{
    if (x_out == NULL || y_out == NULL) return 0;
    if (pcc_last_click == 0) return 0;
    *x_out = (int64_t)(int32_t)(pcc_last_click & 0xFFFFFFFFu);
    *y_out = (int64_t)(int32_t)((pcc_last_click >> 32) & 0xFFFFFFFFu);
    pcc_last_click = 0;
    return 1;
  }}
}}

int64_t pcc_gui_metal_window_is_closed(void *handle) {{
  @autoreleasepool {{
    if (handle == NULL) return 1;
    NSWindow *win = (__bridge NSWindow *)handle;
    /* A hidden window means the user dismissed it.  (Do NOT use
       windowNumber: an ordered-front window that has not yet been mapped
       to the window server reports 0, which would false-positive.) */
    if (![win isVisible]) return 1;
    return 0;
  }}
}}

int64_t pcc_gui_metal_window_close(void *handle) {{
  @autoreleasepool {{
    if (handle == NULL) return -1;
    NSWindow *win = (__bridge NSWindow *)handle;
    [win close];
    CFRelease((__bridge CFTypeRef)win);
    return 0;
  }}
}}
"""
def metal_render_bridge_sha256() -> str:
    return hashlib.sha256(metal_render_bridge_source().encode("utf-8")).hexdigest()


def write_metal_render_bridge(out_dir: str | Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "pcc_gui_metal_render_bridge.m"
    out.write_text(metal_render_bridge_source(), encoding="utf-8")
    return out
