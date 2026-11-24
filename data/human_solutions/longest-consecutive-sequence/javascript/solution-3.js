let nums = [/* Input array */];
let longest = 0;
let numSet = new Set(nums);

for (let i = 0; i < nums.length; i++) {
    let currentNum = nums[i];
    if (!numSet.has(currentNum - 1)) {
        let consecutive = 0;
        let nextConsecutive = currentNum;
        
        while (numSet.has(nextConsecutive)) {
            consecutive++;
            nextConsecutive++;
        }
        
        longest = Math.max(longest, consecutive);
    }
}

return longest;