int func(){
    int a = 3;
    int b = 4;

    if (b > a){
        b += 3;
    }
    return b - a ;
}

int main(){
    return func();
}
