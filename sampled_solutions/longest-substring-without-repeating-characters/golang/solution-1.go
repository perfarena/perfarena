func lengthOfLongestSubstring(s string) int {
    charMap := make(map[rune]int)
    start, maxLen := 0, 0

    for end, char := range s {
        if index, found := charMap[char]; found && index >= start {
            start = index + 1
        }

        currentLen := end - start + 1
        if currentLen > maxLen {
            maxLen = currentLen
        }

        charMap[char] = end
    }

    return maxLen
}