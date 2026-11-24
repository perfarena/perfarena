function topKFrequent(nums, K) {
    const freqMap = new Map();
    
    // Count frequencies of each unique element
    for (const num of nums) {
        freqMap.set(num, (freqMap.get(num) || 0) + 1);
    }
    
    // Sort keys based on frequencies in descending order
    const sortedKeys = Object.keys(freqMap).sort((a, b) => freqMap.get(b) - freqMap.get(a));
    
    // Take the first K elements as the result
    return sortedKeys.slice(0, K);
}