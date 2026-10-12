int[] preSums = new int[nums.length];
preSums[0] = 0;

for (int i = 1; i < nums.length; i++) {
    preSums[i] = preSums[i - 1] + nums[i - 1];
}