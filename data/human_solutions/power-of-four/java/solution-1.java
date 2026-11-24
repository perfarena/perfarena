public class PowerOfFour {
    public boolean isPowerOfFour(int n) {
        if (n <= 0) {
            return false;
        }
        return (Math.log10(n) / Math.log10(4)) % 1 == 0;
    }
}