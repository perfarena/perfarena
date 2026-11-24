def find_intersection(nums1, nums2):
    result = []
    element_count = {}
    
    # Store elements from nums1 in the dictionary
    for num in nums1:
        if num in element_count:
            element_count[num] += 1
        else:
            element_count[num] = 1
    
    # Check elements from nums2 in the dictionary
    for num in nums2:
        if num in element_count and element_count[num] > 0:
            result.append(num)
            element_count[num] -= 1
    
    return result

# Example usage
nums1 = [1, 2, 2, 1]
nums2 = [2, 2]
print(find_intersection(nums1, nums2))  # Output: [2, 2]