function longestConsecutive(nums) {
    if (!nums || nums.length === 0) {
        return 0;
    }

    let numSet = new Set(nums);
    let maxSequence = 0;

    for (let num of numSet) {
        if (!numSet.has(num - 1)) {
            let currentNum = num;
            let currentSequence = 1;

            while (numSet.has(currentNum + 1)) {
                currentNum++;
                currentSequence++;
            }

            maxSequence = Math.max(maxSequence, currentSequence);
        }
    }

    return maxSequence;
}

// Example usage
const nums = [100, 99, 4, 200, 1, 3, 2];
console.log(longestConsecutive(nums)); // Output: 4