Pcc
====================

What is this?
--------------------
Pcc is a c compiler based on ply + pycparesr + llvmlite + llvm.   
We can run c program like python: `pcc test.c` to run c code.  
(no header file support).
Pcc was inspired by: https://github.com/eliben/pykaleidoscope. 

Notice
--------------------
1. Some code skeleton is come from pykaleidoscope.  
2. ply and pycparser was Embedded into this project for debug use.  

Development 
--------------------

1. test with llvm-13 on mac. (linux should work)
`LLVM_CONFIG='/usr/local/Cellar/llvm@13/13.0.1/bin/llvm-config' pip install llvmlite==0.39.1`



Run pcc test
--------------------
0. py.test
1. py.test -s test/test_if.py


Run pcc
--------------------
`python -m pcc clang_study/test_arrary.c`


TODO
--------------------

1. finish the full c syntax (too much test cases).
2. the libc function like `printf, memset`.
3. code refactor.

