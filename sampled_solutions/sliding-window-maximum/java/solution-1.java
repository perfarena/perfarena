int[] left = new int[nums.length];
int[] right = new int[nums.length];
int[] result = new int[nums.length - k + 1];

// Populate left array
for (int i = 0; i < nums.length; i++) {
    left[i] = (i % k == 0) ? nums[i] : Math.max(left[i - 1], nums[i]);
}

// Populate right array
for (int i = nums.length - 1; i >= 0; i--) {
    right[i] = (i % k == k - 1 || i == nums.length - 1) ? nums[i] : Math.max(right[i + 1], nums[i]);
}

// Populate result array
for (int i = 0; i < nums.length - k + 1; i++) {
    result[i] = Math.max(right[i], left[i + k - 1]);
}

return result;