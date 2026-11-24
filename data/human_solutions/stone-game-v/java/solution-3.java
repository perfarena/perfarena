int[] result = new int[nums.length];

for (int i = 0; i < nums.length; i++) {
    result[i] = (preSums[i] * i) - (postSums[i] * (nums.length - i - 1));
}
return result;