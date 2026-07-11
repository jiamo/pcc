"""Five-GC bootstrap scheduling is owned by the shared resource lease.

The five full-bootstrap items must remain independently schedulable by xdist.
``tests.python.test_pcc_bootstrap_full`` admits one GC0 cache warmer first, then
allows bounded parallel frontend execution for the remaining backends.  Do not
put the items back into one shared ``xdist_group``: that bypasses the resource
lease and makes a cold matrix strictly serial.
"""
