left = 0;
right = 0;

while (right < nums.length) {
    if (nums[right] !== 0) {
        // Flip numbers at the left with the right
        [nums[left], nums[right]] = [nums[right], nums[left]];
        // Increment the left pointer
        left++;
    }
    // Increment the right pointer
    right++;
}