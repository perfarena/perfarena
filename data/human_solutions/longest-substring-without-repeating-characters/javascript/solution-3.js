function lengthOfLongestSubstring(s) {
    let set = new Set();
    let left = 0;
    let right = 0;
    let maxSubstringLength = 0;

    while (right < s.length) {
        if (!set.has(s[right])) {
            set.add(s[right]);
            maxSubstringLength = Math.max(maxSubstringLength, set.size);
            right++;
        } else {
            set.delete(s[left]);
            left++;
        }
    }

    return maxSubstringLength;
}