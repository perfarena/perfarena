def find_intersection(nums1, nums2):
    S1 = set(nums1)
    S2 = set(nums2)
    
    result = []
    for num in S1:
        if num in S2:
            result.append(num)
    
    return result

# Example usage
nums1 = [1, 2, 2, 1]
nums2 = [2, 2]
print(find_intersection(nums1, nums2))  # Output: [2]