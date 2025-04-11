#ifndef ___MR_BOX_PERIPHERAL_BOARD_CONFIG_VALIDATE___
#define ___MR_BOX_PERIPHERAL_BOARD_CONFIG_VALIDATE___

namespace mr_box_peripheral_board {
namespace config_validate {

template <typename NodeT>
class Validator : public MessageValidator<0> {
public:

  Validator() {
  }

  void set_node(NodeT &node) {
  }
};

}  // namespace config_validate
}  // namespace mr_box_peripheral_board

#endif  // #ifndef ___MR_BOX_PERIPHERAL_BOARD_CONFIG_VALIDATE___
    
