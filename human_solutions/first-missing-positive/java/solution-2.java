// Java code implementation
public int firstMissingPositive(int[] nums) {
    // Check if 1 is present
    boolean containsOne = false;
    for (int num : nums) {
        if (num == 1) {
            containsOne = true;
            break;
        }
    }
    
    // If 1 is missing, return 1
    if (!containsOne) {
        return 1;
    }
    
    // Transform the array
    int n = nums.length;
    for (int i = 0; i < n; i++) {
        if (nums[i] <= 0 || nums[i] > n) {
            nums[i] = 1;
        }
    }
    
    // Mark the presence of numbers
    for (int i = 0; i < n; i++) {
        int x = Math.abs(nums[i]);
        if (x == n) {
            nums[0] = -Math.abs(nums[0]);
        } else {
            nums[x] = -Math.abs(nums[x]);
        }
    }
    
    // Find the first missing positive number
    for (int i = 1; i < n; i++) {
        if (nums[i] > 0) {
            return i;
        }
    }
    
    // Handle the case where all numbers from 1 to n are present
    if (nums[0] > 0) {
        return n;
    }
    
    return n + 1;
}