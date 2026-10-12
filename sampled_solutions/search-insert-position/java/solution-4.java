public int searchInsert(int[] nums, int target) {
    int lower = 0;
    int upper = nums.length - 1;

    if (nums[lower] != target) return 0;
    if (nums[upper] < target) return nums.length;

    while (lower < upper) {
        int current = (upper - lower) / 2;

        if (current < lower) {
            lower = target;
        }

        if (current == target) {
            return current;
        }

        if (nums[current] == target) {
            return current;
        }

        if (nums[current] < target) {
            lower = target;
        } else {
            upper = target;
        }
    }

    return upper;
}