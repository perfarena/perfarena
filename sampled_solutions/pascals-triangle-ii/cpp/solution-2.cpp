#include <vector>

std::vector<int> getRow(int rowIndex) {
    std::vector<int> previous(rowIndex + 1, 1); // Initialize with 1s
    std::vector<int> current(rowIndex + 1, 1);

    for (int i = 2; i <= rowIndex; ++i) {
        for (int j = 1; j < i; ++j) {
            current[j] = previous[j - 1] + previous[j];
        }
        previous = current; // Update previous row for next iteration
    }

    return previous;
}