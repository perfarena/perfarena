Arrays.sort(nums);

for (int number : nums) {
    if (number > 0) {
        // Check if the number is the missing positive integer
        if (number == missingNumber) {
            missingNumber++;
        } else if (number > missingNumber) {
            break;
        }
    }
}

return missingNumber;