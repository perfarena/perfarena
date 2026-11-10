def findMaxConsecutiveOnes(AR, K):
    left = 0
    right = 0
    maxConsecutiveOnes = 0
    zerosToFlip = 0

    while right < len(AR):
        if AR[right] == 1:
            right += 1
        elif zerosToFlip < K:
            zerosToFlip += 1
            right += 1
        else:
            maxConsecutiveOnes = max(maxConsecutiveOnes, right - left)
            if AR[left] == 0:
                zerosToFlip -= 1
            left += 1

    return max(maxConsecutiveOnes, right - left)

# Test the function
AR = [1, 0, 1, 1, 0, 1]
K = 2
print(findMaxConsecutiveOnes(AR, K))  # Output: 4