int minOperations(int[] nums) {
    int n = nums.length;
    int[] frequency = new int[100001]; // Assuming constraints
    for (int num : nums) {
        frequency[num]++;
    }

    int operations = 0;
    int result = 0;

    for (int i = 100000; i > 0; i--) {
        if (frequency[i] > 0) {
            operations += frequency[i];
            result += operations - frequency[i];
        }
    }

    return result;
}