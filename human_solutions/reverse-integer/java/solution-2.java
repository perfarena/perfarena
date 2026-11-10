public int reverse(int x) {
    long finalN = 0;
    while (x != 0) {
        finalN = finalN * 10 + x % 10;
        x /= 10;
        if (finalN > Integer.MAX_VALUE || finalN < Integer.MIN_VALUE) {
            return 0;
        }
    }
    return (int) finalN;
}