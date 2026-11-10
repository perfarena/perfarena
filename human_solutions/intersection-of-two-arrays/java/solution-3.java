// Create a HashSet to store unique elements
HashSet<Integer> set = new HashSet<>();

// Iterate over nums1 array and add unique elements to the set
for (int i = 0; i < nums1.length; i++) {
    set.add(nums1[i]);
}

// Create a list to store the intersection elements
List<Integer> list = new ArrayList<>();

// Iterate over nums2 array to find common elements
for (int i = 0; i < nums2.length; i++) {
    if (set.contains(nums2[i])) {
        list.add(nums2[i]);
        set.remove(nums2[i]); // Remove the element from set to maintain uniqueness
    }
}

// Convert the list to an array
int[] result = new int[list.size()];
for (int i = 0; i < list.size(); i++) {
    result[i] = list.get(i);
}

// Return the array containing the intersection elements
return result;