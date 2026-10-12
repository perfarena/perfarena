function longestConsecutive(nums) {
    if (nums.length === 0) return 0;
    
    const numSet = new Set(nums);
    const starters = [];
    let tempCount = 1;
    let longestCount = 1;
    
    for (const num of numSet.keys()) {
        if (!numSet.has(num - 1)) {
            starters.push(num);
        }
    }
    
    for (const starter of starters) {
        let breaker = true;
        let tempStarter = starter;
        
        while (breaker) {
            if (numSet.has(tempStarter + 1)) {
                tempCount++;
                longestCount = Math.max(longestCount, tempCount);
                tempStarter++;
            } else {
                tempCount = 1;
                breaker = false;
            }
        }
    }
    
    return longestCount;
}