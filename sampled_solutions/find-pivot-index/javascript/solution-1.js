function findPivotIndex(nums) {
    let totalSum = 0;
    let leftSum = 0;

    // Calculate total sum
    nums.forEach(num => totalSum += num);

    for (let i = 0; i < nums.length; i++) {
        if (totalSum - leftSum - nums[i] === leftSum) {
            return i;
        }
        leftSum += nums[i];
    }

    return -1;
}

// Test the function
console.log(findPivotIndex([1, 7, 3, 6, 5, 6])); // Output: 3
console.log(findPivotIndex([1, 2, 3])); // Output: -1