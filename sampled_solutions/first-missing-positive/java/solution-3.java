public int firstMissingPositive(int[] nums) {
    if (nums == null || nums.length == 0) {
        return 1;
    }

    Arrays.sort(nums);

    int temp = 0;
    for (int num : nums) {
        if (num <= 0) {
            continue;
        }

        int diff = num - temp;
        if (diff > 1) {
            return temp + 1;
        }

        if (diff == 1) {
            temp = num;
        }
    }

    return temp + 1;
}