#include <stdio.h>

int addDigits(int num) {
    int sum = 0;
    
    while (num) {
        sum += num % 10;
        num /= 10;
        
        if (num == 0 && sum >= 10) {
            num = sum;
            sum = 0;
        }
    }
    
    return sum;
}

int main() {
    int num = 38;
    int result = addDigits(num);
    
    printf("Output for %d is %d\n", num, result);
    
    return 0;
}