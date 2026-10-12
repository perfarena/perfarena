class Solution:
    def nextGreatestLetter(self, letters, target):
        start, end = 0, len(letters)
        
        while start < end:
            mid = (start + end) // 2
            if letters[mid] <= target:
                start = mid + 1
            else:
                end = mid
        
        return letters[start % len(letters)]