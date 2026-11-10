// Optimized Solution
function twoSumOptimized(nums, target) {
    const complementMap = new Map();
    
    for (let i = 0; i < nums.length; i++) {
        const complement = target - nums[i];
        
        if (complementMap.has(nums[i])) {
            return [complementMap.get(nums[i]), i];
        }
        
        complementMap.set(complement, i);
    }
    
    return [];
}