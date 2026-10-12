int low = 0;
int high = nums.length - 1;

while (high >= low) {
    int middle = (high + low) / 2;

    if (nums[middle] > target) {
        high = middle - 1;
    } else if (nums[middle] < target) {
        low = middle + 1;
    } else {
        return middle;
    }
}

return low;