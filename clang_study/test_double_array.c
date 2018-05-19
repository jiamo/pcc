int main(){
    int i = 1;
    int j = 1;
    int a[100][2];
    int len = 100;
    int len2 = 10;
    int sum =  0 ;

    for(i = 0; i < len ; i++){
        a[i][0] = i + 1;
    }

    for(i = 0; i < len ; i++){
        sum +=  a[i][0];
    }

    return sum ;
}
