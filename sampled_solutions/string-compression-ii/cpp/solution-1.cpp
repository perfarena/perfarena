#include <vector>
#include <algorithm>

int widestVerticalArea(std::vector<std::vector<int>>& points) {
    std::vector<int> temp;
    for (const auto& point : points) {
        temp.push_back(point[0]); // Add x-coordinates to temporary array
    }
    
    std::sort(temp.begin(), temp.end()); // Sort the temporary array
    
    int result = 0;
    for (int i = 1; i < temp.size(); ++i) {
        result = std::max(result, temp[i] - temp[i - 1]); // Calculate maximum width
    }
    
    return result;
}