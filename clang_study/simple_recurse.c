int f(int x){
    if (x < 1){
        return 1;
    }
    else{
        return f(x - 1);
    }
}

int main(){
    int x = 5;
    int b;
    b = f(x);
    return b;
}