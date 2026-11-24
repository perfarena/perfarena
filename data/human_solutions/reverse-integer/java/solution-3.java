public int reverse(int x) {
    int result = 0;
    boolean isNegative = x < 0;
    if (isNegative) x = -x;

    while (x > 0) {
        int lastDigit = x % 10;
        int resultNew = result * 10 + lastDigit;
        
        if (resultNew / 10 != result) return 0; // Check for 32-bit range
        
        result = resultNew;
        x /= 10;
    }

    return isNegative ? -result : result;
}