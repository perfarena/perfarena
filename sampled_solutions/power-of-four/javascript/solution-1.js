function isPowerOfFour(n) {
    if (n <= 0) {
        return false;
    }
    
    return Number.isInteger(Math.log10(n) / Math.log10(4));
}