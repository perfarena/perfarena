public boolean isPowerOfThree(int n) {
    if (n <= 0) {
        return false;
    }
    int maxPowerOfThree = (int) Math.pow(3, (int) (Math.log(Integer.MAX_VALUE) / Math.log(3)));
    return maxPowerOfThree % n == 0;
}