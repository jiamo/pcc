import sys
import os

print("argv0-basename:", os.path.basename(sys.argv[0]))
print("argc:", len(sys.argv))
for a in sys.argv[1:]:
    print("arg:", a)
print("exists-py-tree:", os.path.isdir("pcc"))
