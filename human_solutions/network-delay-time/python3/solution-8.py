def next_greatest_letter(letters, target):
    left, right = 0, len(letters) - 1
    
    while left <= right:
        mid = (left + right) // 2
        
        if target < letters[mid]:
            right = mid - 1
        else:
            left = mid + 1
    
    return letters[left % len(letters)]

# Test the function
letters = ['a', 'b', 'd', 'f', 'z']
target = 'c'
result = next_greatest_letter(letters, target)
print(result)  # Output should be 'd'