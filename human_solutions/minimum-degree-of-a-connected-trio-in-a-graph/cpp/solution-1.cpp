int equalizeArray(vector<int>& nums) {
    int operation = 0;
    sort(nums.begin(), nums.end());
    int n = nums.size();
    
    for (int i = n - 1; i >= 0; i--) {
        if (i == 0 || nums[i] == nums[i - 1]) {
            break;
        } else {
            // Perform operation to make elements equal
            // Increment operation count
            operation += 2;
        }
    }
    
    return operation;
}