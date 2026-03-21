#ifndef STACK_H
#define STACK_H

#define STACK_SIZE 64

int _stack[STACK_SIZE];
int _sp = -1;

void push(int val) { _sp++; _stack[_sp] = val; }
int pop() { int v = _stack[_sp]; _sp--; return v; }
int peek() { return _stack[_sp]; }
int empty() { return _sp < 0; }

#endif
