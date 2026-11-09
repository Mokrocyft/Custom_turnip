#!/usr/bin/env python3
"""
OnePlus 15 Adreno 840 (Snapdragon 8 Elite) Apex Max-Performance KGSL & Autotune Suite.

Forces KGSL_PROP_PWR_CONSTRAINT = PWR_MAX on queue creation and re-asserts it
every 40 ms on submit. Disables KGSL governor downclocking via KGSL_PROP_PWRCTRL
while queues are active to prevent GPU frequency drop to 525 MHz idle.
"""

import sys

KGSL_FILE = "src/freedreno/vulkan/tu_knl_kgsl.cc"
AUTOTUNE_FILE = "src/freedreno/vulkan/tu_autotune.cc"

try:
    with open(KGSL_FILE, "r", encoding="utf-8") as f:
        kgsl = f.read()
except OSError as e:
    print(f"apply_perf_variant: cannot read {KGSL_FILE}: {e}")
    sys.exit(0)

kgsl_changed = False

HELPER_BLOCK = """
#ifndef KGSL_PROP_PWR_CONSTRAINT
#define KGSL_PROP_PWR_CONSTRAINT 0x12
#endif
#ifndef KGSL_PROP_PWRCTRL
#define KGSL_PROP_PWRCTRL 0x1E
#endif
#ifndef KGSL_CONSTRAINT_PWR_MAX
#define KGSL_CONSTRAINT_PWR_MAX 2
#endif
#ifndef KGSL_CONSTRAINT_PWRLEVEL
#define KGSL_CONSTRAINT_PWRLEVEL 1
#endif
#ifndef KGSL_CONTEXT_PWR_CONSTRAINT
#define KGSL_CONTEXT_PWR_CONSTRAINT 0x00080000
#endif

struct kgsl_device_constraint_pwrlevel {
   uint32_t level;
};

struct kgsl_device_constraint {
   uint32_t type;
   uint32_t context_id;
   void *data;
   size_t size;
};

static int
wnturnip_set_pwr_max_constraint(int fd, uint32_t context_id)
{
   struct kgsl_device_constraint_pwrlevel pwrlevel = {
      .level = KGSL_CONSTRAINT_PWR_MAX,
   };
   struct kgsl_device_constraint constraint = {
      .type = KGSL_CONSTRAINT_PWRLEVEL,
      .context_id = context_id,
      .data = (void *)&pwrlevel,
      .size = sizeof(pwrlevel),
   };
   struct kgsl_device_getproperty prop = {
      .type = KGSL_PROP_PWR_CONSTRAINT,
      .value = (void *)&constraint,
      .sizebytes = sizeof(constraint),
   };

   return safe_ioctl(fd, IOCTL_KGSL_SETPROPERTY, &prop);
}

static bool
wnturnip_should_refresh_pwr_max_constraint(void)
{
   static int64_t last_pwr_refresh_ns = 0;
   static const int64_t pwr_refresh_interval_ns = 40 * 1000 * 1000;
   const int64_t now_ns = os_time_get_nano();
   const int64_t last_ns = p_atomic_read(&last_pwr_refresh_ns);

   if (last_ns != 0 && now_ns - last_ns < pwr_refresh_interval_ns)
      return false;

   return p_atomic_cmpxchg(&last_pwr_refresh_ns, last_ns, now_ns) == last_ns;
}

static int
wnturnip_set_pwrctrl_enabled(struct tu_device *dev, uint32_t enable)
{
   struct kgsl_device_getproperty prop = {
      .type = KGSL_PROP_PWRCTRL,
      .value = (void *)&enable,
      .sizebytes = sizeof(enable),
   };

   return safe_ioctl(dev->physical_device->local_fd, IOCTL_KGSL_SETPROPERTY, &prop);
}

static int32_t wnturnip_pwrctrl_force_users = 0;
"""

SUBMITQUEUE_ANCHOR = """static int
kgsl_submitqueue_new(struct tu_device *dev,"""

if "wnturnip_set_pwr_max_constraint" not in kgsl:
    if SUBMITQUEUE_ANCHOR in kgsl:
        kgsl = kgsl.replace(SUBMITQUEUE_ANCHOR, HELPER_BLOCK + "\n" + SUBMITQUEUE_ANCHOR, 1)
        kgsl_changed = True
        print(f"  {KGSL_FILE}: inserted PWR_MAX constraint helpers")
    else:
        print(f"  WARNING: {KGSL_FILE}: submitqueue anchor absent", file=sys.stderr)

FLAGS_OLD = """      .flags = KGSL_CONTEXT_SAVE_GMEM |
               KGSL_CONTEXT_NO_GMEM_ALLOC |
               KGSL_CONTEXT_PREAMBLE,"""
FLAGS_NEW = """      .flags = KGSL_CONTEXT_SAVE_GMEM |
               KGSL_CONTEXT_NO_GMEM_ALLOC |
               KGSL_CONTEXT_PREAMBLE | KGSL_CONTEXT_PWR_CONSTRAINT,"""

if FLAGS_OLD in kgsl:
    kgsl = kgsl.replace(FLAGS_OLD, FLAGS_NEW, 1)
    kgsl_changed = True
    print(f"  {KGSL_FILE}: added KGSL_CONTEXT_PWR_CONSTRAINT flag")
elif "KGSL_CONTEXT_PWR_CONSTRAINT" in kgsl:
    print(f"  {KGSL_FILE}: KGSL_CONTEXT_PWR_CONSTRAINT flag already present")

QUEUE_ID_OLD = """   queue->msm_queue_id = req.drawctxt_id;

   return 0;"""

QUEUE_ID_NEW = """   queue->msm_queue_id = req.drawctxt_id;

   /* WN-Turnip: Set PWR_MAX constraint and disable KGSL governor for max clock */
   if (wnturnip_set_pwr_max_constraint(dev->physical_device->local_fd, req.drawctxt_id) != 0) {
      mesa_logw("Failed to set initial PWR_MAX constraint on context %u", req.drawctxt_id);
   }
   if (p_atomic_inc_return(&wnturnip_pwrctrl_force_users) == 1) {
      int pwr_ret = wnturnip_set_pwrctrl_enabled(dev, 0);
      if (pwr_ret)
         mesa_logw("WN-Turnip: Failed to disable KGSL governor via KGSL_PROP_PWRCTRL: %s", strerror(errno));
   }

   return 0;"""

if QUEUE_ID_OLD in kgsl:
    kgsl = kgsl.replace(QUEUE_ID_OLD, QUEUE_ID_NEW, 1)
    kgsl_changed = True
    print(f"  {KGSL_FILE}: added queue creation PWR_MAX setting and governor control")

SUBMIT_ANCHOR = "   req.context_id = queue->msm_queue_id;\n"
SUBMIT_NEW = """   req.context_id = queue->msm_queue_id;
   if (wnturnip_should_refresh_pwr_max_constraint()) {
      (void) wnturnip_set_pwr_max_constraint(dev->physical_device->local_fd, queue->msm_queue_id);
   }
"""

if SUBMIT_ANCHOR in kgsl and "wnturnip_should_refresh_pwr_max_constraint" not in kgsl:
    kgsl = kgsl.replace(SUBMIT_ANCHOR, SUBMIT_NEW, 1)
    kgsl_changed = True
    print(f"  {KGSL_FILE}: added periodic PWR_MAX refresh on submit")

CLOSE_OLD = """static void
kgsl_submitqueue_close(struct tu_device *dev, struct tu_queue *queue)
{
   struct kgsl_drawctxt_destroy req = {
      .drawctxt_id = queue->msm_queue_id,
   };

   safe_ioctl(dev->physical_device->local_fd, IOCTL_KGSL_DRAWCTXT_DESTROY, &req);
}"""

CLOSE_NEW = """static void
kgsl_submitqueue_close(struct tu_device *dev, struct tu_queue *queue)
{
   struct kgsl_drawctxt_destroy req = {
      .drawctxt_id = queue->msm_queue_id,
   };

   if (p_atomic_dec_return(&wnturnip_pwrctrl_force_users) == 0) {
      int pwr_ret = wnturnip_set_pwrctrl_enabled(dev, 1);
      if (pwr_ret)
         mesa_logw("WN-Turnip: Failed to re-enable KGSL governor via KGSL_PROP_PWRCTRL: %s", strerror(errno));
   }

   safe_ioctl(dev->physical_device->local_fd, IOCTL_KGSL_DRAWCTXT_DESTROY, &req);
}"""

if CLOSE_OLD in kgsl:
    kgsl = kgsl.replace(CLOSE_OLD, CLOSE_NEW, 1)
    kgsl_changed = True
    print(f"  {KGSL_FILE}: re-enable governor on queue close")

if kgsl_changed:
    with open(KGSL_FILE, "w", encoding="utf-8") as f:
        f.write(kgsl)

try:
    with open(AUTOTUNE_FILE, "r", encoding="utf-8") as f:
        autotune = f.read()
except OSError as e:
    print(f"apply_perf_variant: cannot read {AUTOTUNE_FILE}: {e}")
    sys.exit(0)

at_changed = False

OLD_DC = "if (cmd_buffer->state.rp.drawcall_count > 5)"
NEW_DC = "if (cmd_buffer->state.rp.drawcall_count > 10)"
if NEW_DC in autotune:
    print(f"  {AUTOTUNE_FILE}: drawcall threshold already at 10")
elif OLD_DC in autotune:
    autotune = autotune.replace(OLD_DC, NEW_DC, 1)
    at_changed = True
    print(f"  {AUTOTUNE_FILE}: raised drawcall threshold 5 -> 10")

OLD_BW = "gmem_bandwidth = (gmem_bandwidth * 11 + total_draw_call_bandwidth) / 10;"
NEW_BW = "gmem_bandwidth = (gmem_bandwidth * 10 + total_draw_call_bandwidth) / 10;"
if OLD_BW in autotune:
    autotune = autotune.replace(OLD_BW, NEW_BW, 1)
    at_changed = True
    print(f"  {AUTOTUNE_FILE}: reduced bandwidth multiplier 11 -> 10")
elif "* 10 + total" in autotune:
    print(f"  {AUTOTUNE_FILE}: bandwidth multiplier already at 10")

if at_changed:
    with open(AUTOTUNE_FILE, "w", encoding="utf-8") as f:
        f.write(autotune)

print("apply_balance_variant.py (Apex Max-Perf): done")
