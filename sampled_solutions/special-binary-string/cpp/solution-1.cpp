#include <vector>
#include <string>

std::vector<int> partitionLabels(std::string s) {
    std::vector<int> last_index(26, -1);
    for (int i = 0; i < s.size(); ++i) {
        last_index[s[i] - 'a'] = i;
    }

    std::vector<int> result;
    int start = 0, end = 0;
    for (int i = 0; i < s.size(); ++i) {
        end = std::max(end, last_index[s[i] - 'a']);
        if (i == end) {
            result.push_back(end - start + 1);
            start = i + 1;
        }
    }

    return result;
}