function moveZeroes(nums) {
    let anchor = 0;
    for (let explorer = 0; explorer < nums.length; explorer++) {
        if (nums[explorer] !== 0) {
            // Swap non-zero element with anchor
            let temp = nums[anchor];
            nums[anchor] = nums[explorer];
            nums[explorer] = temp;
            anchor++;
        }
    }
}