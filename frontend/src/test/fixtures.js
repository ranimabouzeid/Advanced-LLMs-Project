// Fixed snapshots exported from the validated Python domain. No backend needed at test runtime.
export const initial = {
  "warehouse": {
    "width": 10,
    "height": 10,
    "revision": 0,
    "robots": [
      {
        "id": "robot-1",
        "position": {
          "x": 0,
          "y": 0
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      },
      {
        "id": "robot-2",
        "position": {
          "x": 0,
          "y": 1
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      },
      {
        "id": "robot-3",
        "position": {
          "x": 0,
          "y": 2
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      }
    ],
    "obstacles": [
      {
        "x": 3,
        "y": 2
      },
      {
        "x": 3,
        "y": 3
      },
      {
        "x": 3,
        "y": 4
      },
      {
        "x": 3,
        "y": 5
      },
      {
        "x": 3,
        "y": 6
      },
      {
        "x": 3,
        "y": 7
      },
      {
        "x": 6,
        "y": 2
      },
      {
        "x": 6,
        "y": 3
      },
      {
        "x": 6,
        "y": 4
      },
      {
        "x": 6,
        "y": 5
      },
      {
        "x": 6,
        "y": 6
      },
      {
        "x": 6,
        "y": 7
      }
    ],
    "blocked_cells": [],
    "dropoff_locations": [
      {
        "x": 9,
        "y": 0
      },
      {
        "x": 9,
        "y": 9
      }
    ],
    "orders": []
  },
  "command": "plan",
  "execution_requested": false,
  "order_selection": null,
  "selected_robot_id": null,
  "delivery_plan": null,
  "planning_outcome": "not_planned",
  "safety": null,
  "replan_count": 0,
  "max_replans": 3,
  "run_outcome": "idle",
  "error_message": null,
  "node_activity": []
};
export const ordered = {
  "warehouse": {
    "width": 10,
    "height": 10,
    "revision": 1,
    "robots": [
      {
        "id": "robot-1",
        "position": {
          "x": 0,
          "y": 0
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      },
      {
        "id": "robot-2",
        "position": {
          "x": 0,
          "y": 1
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      },
      {
        "id": "robot-3",
        "position": {
          "x": 0,
          "y": 2
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      }
    ],
    "obstacles": [
      {
        "x": 3,
        "y": 2
      },
      {
        "x": 3,
        "y": 3
      },
      {
        "x": 3,
        "y": 4
      },
      {
        "x": 3,
        "y": 5
      },
      {
        "x": 3,
        "y": 6
      },
      {
        "x": 3,
        "y": 7
      },
      {
        "x": 6,
        "y": 2
      },
      {
        "x": 6,
        "y": 3
      },
      {
        "x": 6,
        "y": 4
      },
      {
        "x": 6,
        "y": 5
      },
      {
        "x": 6,
        "y": 6
      },
      {
        "x": 6,
        "y": 7
      }
    ],
    "blocked_cells": [],
    "dropoff_locations": [
      {
        "x": 9,
        "y": 0
      },
      {
        "x": 9,
        "y": 9
      }
    ],
    "orders": [
      {
        "id": "o",
        "package": {
          "id": "p",
          "pickup": {
            "x": 2,
            "y": 0
          }
        },
        "dropoff": {
          "x": 9,
          "y": 0
        },
        "status": "pending",
        "assigned_robot_id": null
      }
    ]
  },
  "command": "plan",
  "execution_requested": false,
  "order_selection": null,
  "selected_robot_id": null,
  "delivery_plan": null,
  "planning_outcome": "not_planned",
  "safety": null,
  "replan_count": 0,
  "max_replans": 3,
  "run_outcome": "idle",
  "error_message": null,
  "node_activity": []
};
export const ready = {
  "warehouse": {
    "width": 10,
    "height": 10,
    "revision": 1,
    "robots": [
      {
        "id": "robot-1",
        "position": {
          "x": 0,
          "y": 0
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      },
      {
        "id": "robot-2",
        "position": {
          "x": 0,
          "y": 1
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      },
      {
        "id": "robot-3",
        "position": {
          "x": 0,
          "y": 2
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      }
    ],
    "obstacles": [
      {
        "x": 3,
        "y": 2
      },
      {
        "x": 3,
        "y": 3
      },
      {
        "x": 3,
        "y": 4
      },
      {
        "x": 3,
        "y": 5
      },
      {
        "x": 3,
        "y": 6
      },
      {
        "x": 3,
        "y": 7
      },
      {
        "x": 6,
        "y": 2
      },
      {
        "x": 6,
        "y": 3
      },
      {
        "x": 6,
        "y": 4
      },
      {
        "x": 6,
        "y": 5
      },
      {
        "x": 6,
        "y": 6
      },
      {
        "x": 6,
        "y": 7
      }
    ],
    "blocked_cells": [],
    "dropoff_locations": [
      {
        "x": 9,
        "y": 0
      },
      {
        "x": 9,
        "y": 9
      }
    ],
    "orders": [
      {
        "id": "o",
        "package": {
          "id": "p",
          "pickup": {
            "x": 2,
            "y": 0
          }
        },
        "dropoff": {
          "x": 9,
          "y": 0
        },
        "status": "pending",
        "assigned_robot_id": null
      }
    ]
  },
  "command": "plan",
  "execution_requested": false,
  "order_selection": {
    "order_id": "o",
    "explanation": "First pending order"
  },
  "selected_robot_id": "robot-1",
  "delivery_plan": {
    "order_id": "o",
    "robot_id": "robot-1",
    "pickup_route": [
      {
        "x": 0,
        "y": 0
      },
      {
        "x": 1,
        "y": 0
      },
      {
        "x": 2,
        "y": 0
      }
    ],
    "delivery_route": [
      {
        "x": 2,
        "y": 0
      },
      {
        "x": 3,
        "y": 0
      },
      {
        "x": 4,
        "y": 0
      },
      {
        "x": 5,
        "y": 0
      },
      {
        "x": 6,
        "y": 0
      },
      {
        "x": 7,
        "y": 0
      },
      {
        "x": 8,
        "y": 0
      },
      {
        "x": 9,
        "y": 0
      }
    ],
    "total_steps": 9,
    "warehouse_revision": 1
  },
  "planning_outcome": "planned",
  "safety": {
    "route_valid": true,
    "collision_risk": false,
    "reasons": [],
    "conflicts": []
  },
  "replan_count": 0,
  "max_replans": 3,
  "run_outcome": "ready",
  "error_message": null,
  "node_activity": [
    {
      "node": "order",
      "status": "completed",
      "message": "order completed"
    },
    {
      "node": "fleet",
      "status": "completed",
      "message": "fleet completed"
    },
    {
      "node": "route",
      "status": "completed",
      "message": "route completed"
    },
    {
      "node": "safety",
      "status": "completed",
      "message": "safety completed"
    }
  ]
};
export const blocked = {
  "warehouse": {
    "width": 10,
    "height": 10,
    "revision": 2,
    "robots": [
      {
        "id": "robot-1",
        "position": {
          "x": 0,
          "y": 0
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      },
      {
        "id": "robot-2",
        "position": {
          "x": 0,
          "y": 1
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      },
      {
        "id": "robot-3",
        "position": {
          "x": 0,
          "y": 2
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      }
    ],
    "obstacles": [
      {
        "x": 3,
        "y": 2
      },
      {
        "x": 3,
        "y": 3
      },
      {
        "x": 3,
        "y": 4
      },
      {
        "x": 3,
        "y": 5
      },
      {
        "x": 3,
        "y": 6
      },
      {
        "x": 3,
        "y": 7
      },
      {
        "x": 6,
        "y": 2
      },
      {
        "x": 6,
        "y": 3
      },
      {
        "x": 6,
        "y": 4
      },
      {
        "x": 6,
        "y": 5
      },
      {
        "x": 6,
        "y": 6
      },
      {
        "x": 6,
        "y": 7
      }
    ],
    "blocked_cells": [
      {
        "x": 5,
        "y": 0
      }
    ],
    "dropoff_locations": [
      {
        "x": 9,
        "y": 0
      },
      {
        "x": 9,
        "y": 9
      }
    ],
    "orders": [
      {
        "id": "o",
        "package": {
          "id": "p",
          "pickup": {
            "x": 2,
            "y": 0
          }
        },
        "dropoff": {
          "x": 9,
          "y": 0
        },
        "status": "pending",
        "assigned_robot_id": null
      }
    ]
  },
  "command": "plan",
  "execution_requested": false,
  "order_selection": {
    "order_id": "o",
    "explanation": "First pending order"
  },
  "selected_robot_id": "robot-1",
  "delivery_plan": {
    "order_id": "o",
    "robot_id": "robot-1",
    "pickup_route": [
      {
        "x": 0,
        "y": 0
      },
      {
        "x": 1,
        "y": 0
      },
      {
        "x": 2,
        "y": 0
      }
    ],
    "delivery_route": [
      {
        "x": 2,
        "y": 0
      },
      {
        "x": 3,
        "y": 0
      },
      {
        "x": 4,
        "y": 0
      },
      {
        "x": 5,
        "y": 0
      },
      {
        "x": 6,
        "y": 0
      },
      {
        "x": 7,
        "y": 0
      },
      {
        "x": 8,
        "y": 0
      },
      {
        "x": 9,
        "y": 0
      }
    ],
    "total_steps": 9,
    "warehouse_revision": 1
  },
  "planning_outcome": "stale",
  "safety": null,
  "replan_count": 0,
  "max_replans": 3,
  "run_outcome": "idle",
  "error_message": null,
  "node_activity": [
    {
      "node": "order",
      "status": "completed",
      "message": "order completed"
    },
    {
      "node": "fleet",
      "status": "completed",
      "message": "fleet completed"
    },
    {
      "node": "route",
      "status": "completed",
      "message": "route completed"
    },
    {
      "node": "safety",
      "status": "completed",
      "message": "safety completed"
    }
  ]
};
export const replacement = {
  "warehouse": {
    "width": 10,
    "height": 10,
    "revision": 2,
    "robots": [
      {
        "id": "robot-1",
        "position": {
          "x": 0,
          "y": 0
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      },
      {
        "id": "robot-2",
        "position": {
          "x": 0,
          "y": 1
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      },
      {
        "id": "robot-3",
        "position": {
          "x": 0,
          "y": 2
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      }
    ],
    "obstacles": [
      {
        "x": 3,
        "y": 2
      },
      {
        "x": 3,
        "y": 3
      },
      {
        "x": 3,
        "y": 4
      },
      {
        "x": 3,
        "y": 5
      },
      {
        "x": 3,
        "y": 6
      },
      {
        "x": 3,
        "y": 7
      },
      {
        "x": 6,
        "y": 2
      },
      {
        "x": 6,
        "y": 3
      },
      {
        "x": 6,
        "y": 4
      },
      {
        "x": 6,
        "y": 5
      },
      {
        "x": 6,
        "y": 6
      },
      {
        "x": 6,
        "y": 7
      }
    ],
    "blocked_cells": [
      {
        "x": 5,
        "y": 0
      }
    ],
    "dropoff_locations": [
      {
        "x": 9,
        "y": 0
      },
      {
        "x": 9,
        "y": 9
      }
    ],
    "orders": [
      {
        "id": "o",
        "package": {
          "id": "p",
          "pickup": {
            "x": 2,
            "y": 0
          }
        },
        "dropoff": {
          "x": 9,
          "y": 0
        },
        "status": "pending",
        "assigned_robot_id": null
      }
    ]
  },
  "command": "execute",
  "execution_requested": false,
  "order_selection": {
    "order_id": "o",
    "explanation": "First pending order"
  },
  "selected_robot_id": "robot-1",
  "delivery_plan": {
    "order_id": "o",
    "robot_id": "robot-1",
    "pickup_route": [
      {
        "x": 0,
        "y": 0
      },
      {
        "x": 1,
        "y": 0
      },
      {
        "x": 2,
        "y": 0
      }
    ],
    "delivery_route": [
      {
        "x": 2,
        "y": 0
      },
      {
        "x": 3,
        "y": 0
      },
      {
        "x": 4,
        "y": 0
      },
      {
        "x": 4,
        "y": 1
      },
      {
        "x": 5,
        "y": 1
      },
      {
        "x": 6,
        "y": 1
      },
      {
        "x": 7,
        "y": 1
      },
      {
        "x": 8,
        "y": 1
      },
      {
        "x": 9,
        "y": 1
      },
      {
        "x": 9,
        "y": 0
      }
    ],
    "total_steps": 11,
    "warehouse_revision": 2
  },
  "planning_outcome": "planned",
  "safety": {
    "route_valid": true,
    "collision_risk": false,
    "reasons": [],
    "conflicts": []
  },
  "replan_count": 0,
  "max_replans": 3,
  "run_outcome": "ready",
  "error_message": null,
  "node_activity": [
    {
      "node": "order",
      "status": "completed",
      "message": "order completed"
    },
    {
      "node": "fleet",
      "status": "completed",
      "message": "fleet completed"
    },
    {
      "node": "route",
      "status": "completed",
      "message": "route completed"
    },
    {
      "node": "safety",
      "status": "completed",
      "message": "safety completed"
    },
    {
      "node": "safety",
      "status": "rejected",
      "message": null
    },
    {
      "node": "route",
      "status": "completed",
      "message": null
    },
    {
      "node": "safety",
      "status": "completed",
      "message": null
    }
  ]
};
export const delivered = {
  "warehouse": {
    "width": 10,
    "height": 10,
    "revision": 2,
    "robots": [
      {
        "id": "robot-1",
        "position": {
          "x": 9,
          "y": 0
        },
        "battery": 91,
        "status": "idle",
        "carried_package_id": null
      },
      {
        "id": "robot-2",
        "position": {
          "x": 0,
          "y": 1
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      },
      {
        "id": "robot-3",
        "position": {
          "x": 0,
          "y": 2
        },
        "battery": 100,
        "status": "idle",
        "carried_package_id": null
      }
    ],
    "obstacles": [
      {
        "x": 3,
        "y": 2
      },
      {
        "x": 3,
        "y": 3
      },
      {
        "x": 3,
        "y": 4
      },
      {
        "x": 3,
        "y": 5
      },
      {
        "x": 3,
        "y": 6
      },
      {
        "x": 3,
        "y": 7
      },
      {
        "x": 6,
        "y": 2
      },
      {
        "x": 6,
        "y": 3
      },
      {
        "x": 6,
        "y": 4
      },
      {
        "x": 6,
        "y": 5
      },
      {
        "x": 6,
        "y": 6
      },
      {
        "x": 6,
        "y": 7
      }
    ],
    "blocked_cells": [],
    "dropoff_locations": [
      {
        "x": 9,
        "y": 0
      },
      {
        "x": 9,
        "y": 9
      }
    ],
    "orders": [
      {
        "id": "o",
        "package": {
          "id": "p",
          "pickup": {
            "x": 2,
            "y": 0
          }
        },
        "dropoff": {
          "x": 9,
          "y": 0
        },
        "status": "delivered",
        "assigned_robot_id": "robot-1"
      }
    ]
  },
  "command": "execute",
  "execution_requested": false,
  "order_selection": null,
  "selected_robot_id": null,
  "delivery_plan": null,
  "planning_outcome": "not_planned",
  "safety": null,
  "replan_count": 0,
  "max_replans": 3,
  "run_outcome": "delivered",
  "error_message": null,
  "node_activity": [
    {
      "node": "order",
      "status": "completed",
      "message": "order completed"
    },
    {
      "node": "fleet",
      "status": "completed",
      "message": "fleet completed"
    },
    {
      "node": "route",
      "status": "completed",
      "message": "route completed"
    },
    {
      "node": "safety",
      "status": "completed",
      "message": "safety completed"
    },
    {
      "node": "execution",
      "status": "completed",
      "message": "Atomic delivery completed"
    }
  ]
};
export const envelope = (state, session_id = 'session-a', outcome) => ({ session_id, state: structuredClone(state), ...(outcome ? { outcome, error: null } : {}) });
export const response = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => structuredClone(data) });
export function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
