# Define two pointers
left = 0
right = len(s) - 1

# Iterate and swap characters
while left < right:
    # Swap characters at the pointers' locations
    temp = s[left]
    s[left] = s[right]
    s[right] = temp
    
    # Move the pointers
    left += 1
    right -= 1