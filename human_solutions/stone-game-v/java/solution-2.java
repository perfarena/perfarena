int[] postSums = new int[nums.length];
postSums[nums.length - 1] = 0;

for (int i = nums.length - 2; i >= 0; i--) {
    postSums[i] = postSums[i + 1] + nums[i + 1];
}