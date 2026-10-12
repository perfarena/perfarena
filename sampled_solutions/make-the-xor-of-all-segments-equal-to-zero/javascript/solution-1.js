function maxProductDifference(nums) {
    let largest = 0, secondLargest = 0, smallest = Infinity, secondSmallest = Infinity;

    for (let i = 0; i < nums.length; i++) {
        let currentNum = nums[i];

        if (currentNum > largest) {
            secondLargest = largest;
            largest = currentNum;
        } else if (currentNum > secondLargest) {
            secondLargest = currentNum;
        }

        if (currentNum < smallest) {
            secondSmallest = smallest;
            smallest = currentNum;
        } else if (currentNum < secondSmallest) {
            secondSmallest = currentNum;
        }
    }

    return (largest * secondLargest) - (smallest * secondSmallest);
}

// Test the function with an example
const nums = [5, 6, 2, 7, 4];
console.log(maxProductDifference(nums)); // Output should be 34