int sum = 0;
for (int n : nums) {
    sum += n;
}
int left = 0, right = sum;
int[] result = new int[nums.length];
for (int i = 0; i < nums.length; i++) {
    int n = nums[i];
    right -= n;
    result[i] = n * i - left + right - n * (nums.length - 1 - i);
    left += n;
}
return result;