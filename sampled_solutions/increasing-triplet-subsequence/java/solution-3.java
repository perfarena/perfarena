public boolean increasingCryptoSubsequence(int[] nums) {
    int Min = Integer.MAX_VALUE;
    int SMin = Integer.MAX_VALUE;

    for (int n : nums) {
        if (n <= Min) {
            Min = n;
        } else if (n <= SMin) {
            SMin = n;
        } else {
            return true;
        }
    }

    return false;
}