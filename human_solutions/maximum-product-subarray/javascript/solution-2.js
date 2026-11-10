// Initialize variables
let Max = nums[0];
let Min = nums[0];
let answer = nums[0];

// Iterate through the array
for (let i = 1; i < nums.length; i++) {
    let temp = Max;
    Max = Math.max(nums[i], Math.max(Max * nums[i], Min * nums[i]));
    Min = Math.min(nums[i], Math.min(temp * nums[i], Min * nums[i]));
    answer = Math.max(answer, Max);
}

return answer;