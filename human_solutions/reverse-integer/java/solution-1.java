public int reverseInteger(int x) {
    boolean negative = false;
    if (x < 0) {
        negative = true;
        x = -x;
    }
    
    long reversed = 0;
    while (x > 0) {
        int digit = x % 10;
        reversed = reversed * 10 + digit;
        x /= 10;
        
        if (reversed > Integer.MAX_VALUE) {
            return 0; // Overflow
        }
    }
    
    if (negative) {
        reversed = -reversed;
    }
    
    return (int) reversed;
}