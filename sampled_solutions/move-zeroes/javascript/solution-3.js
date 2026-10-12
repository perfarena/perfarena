function moveZeros(nums) {
    let lastNonZeroIndex = 0;
    
    // Move non-zero elements to the front
    for (let i = 0; i < nums.length; i++) {
        if (nums[i] !== 0) {
            nums[lastNonZeroIndex] = nums[i];
            lastNonZeroIndex++;
        }
    }
    
    // Fill the remaining positions with zeros
    for (let i = lastNonZeroIndex; i < nums.length; i++) {
        nums[i] = 0;
    }
}